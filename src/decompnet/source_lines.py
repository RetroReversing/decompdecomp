"""Advisory C locations from the exact compiled object, never a debug-flag recompile.

Optimizers coalesce/reorder statements. A location associates an instruction with a
line; it does not prove that editing that line alone will repair the instruction.
ECOFF layout/line deltas: binutils include/coff/sym.h and bfd/ecofflink.c.
"""
import struct
from io import BytesIO
from pathlib import PurePosixPath

from elftools.common.exceptions import DWARFError, ELFError
from elftools.elf.elffile import ELFFile

from . import scoring
from .elfobj import _function_symbols, extract_function


def _basename(value):
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    return PurePosixPath(str(value).replace('\\', '/')).name


def _dwarf(elf, start, size, filename):
    if not elf.has_dwarf_info():
        return []
    spans = []
    for cu in elf.get_dwarf_info().iter_CUs():
        program = elf.get_dwarf_info().line_program_for_CU(cu)
        if program is None:
            continue
        previous = None
        for entry in program.get_entries():
            state = entry.state
            if state is None:
                continue
            if previous and state.address > previous.address:
                index = previous.file - (0 if program.header.version >= 5 else 1)
                files = program.header.file_entry
                if 0 <= index < len(files) and _basename(files[index].name) == _basename(filename):
                    lo, hi = max(start, previous.address), min(start + size, state.address)
                    if lo < hi and previous.line:
                        spans.append((lo - start, hi - start, previous.line))
            previous = None if state.end_sequence else state
    return spans


def _dwarf1(elf, start, size, filename):
    from .dwarf1_lines import _attributes, line_table
    debug, lines = elf.get_section_by_name('.debug'), elf.get_section_by_name('.line')
    if debug is None or lines is None:
        return []
    data, line_data = debug.data(), lines.data()
    order = '<' if elf.little_endian else '>'
    at, spans = 0, []
    while at + 6 <= len(data):
        length, tag = struct.unpack_from(order + 'IH', data, at)
        if length < 6 or at + length > len(data):
            break
        if tag == 0x11:  # DW_TAG_compile_unit
            attrs = _attributes(data, at + 6, at + length, order)
            if _basename(attrs.get(0x38, '')) == _basename(filename) and 0x106 in attrs:
                entries = line_table(line_data, attrs[0x106], elf.little_endian, include_end=True)
                for index, (address, line) in enumerate(entries):
                    if not line:
                        continue
                    end = entries[index + 1][0] if index + 1 < len(entries) else start + size
                    lo, hi = max(start, address), min(start + size, end)
                    if lo < hi:
                        spans.append((lo - start, hi - start, line))
        at += length
    return spans


def _ecoff(elf, data, start, size, name, filename):
    section = elf.get_section_by_name('.mdebug')
    if section is None:
        return []
    order = '<' if elf.little_endian else '>'
    header = struct.unpack_from(order + 'HH23I', data, section['sh_offset'])
    if header[0] != 0x7009:
        return []
    # HDRR offsets are file-relative, as emitted in ELF .mdebug objects.
    lines_at, pd_at, symbols_at, strings_at = header[4], header[8], header[10], header[16]
    files, files_at = header[19], header[20]
    spans = []
    for index in range(min(files, len(data) // 72)):
        fd = struct.unpack_from(order + '10I2H7I', data, files_at + index * 72)
        string_base, sym_base, first_pd, count_pd = fd[2], fd[4], fd[10], fd[11]
        file_at = strings_at + string_base + fd[1]
        filename_here = data[file_at:data.index(b'\0', file_at)]
        if _basename(filename_here) != _basename(filename):
            continue
        for proc in range(min(count_pd, len(data) // 52)):
            pd = struct.unpack_from(order + '9I2H3I', data, pd_at + (first_pd + proc) * 52)
            sym = struct.unpack_from(order + '3I', data, symbols_at + (sym_base + pd[1]) * 12)
            at = strings_at + string_base + sym[0]
            if data[at:data.index(b'\0', at)].decode('utf-8', 'replace') != name:
                continue
            line, address = pd[11], pd[0]
            at = lines_at + fd[17] + pd[13]
            end = min(lines_at + fd[17] + fd[18], len(data))
            while at < end and address < start + size:
                byte = data[at]; at += 1
                delta, count = (byte >> 4), (byte & 15) + 1
                delta = delta - 16 if delta >= 8 else delta
                if delta == -8:
                    delta = int.from_bytes(data[at:at + 2], 'big', signed=True); at += 2
                line += delta
                lo, hi = max(start, address), min(start + size, address + count * 4)
                if lo < hi and line > 0:
                    spans.append((lo - start, hi - start, line))
                address += count * 4
    return spans


def locations(data, name, filename='func.c'):
    """Function-relative byte ranges. Unsupported/ambiguous formats have no locations."""
    try:
        elf = ELFFile(BytesIO(data))
        found = _function_symbols(elf).get(name)
        if found is None:
            return []
        section, start, size = found
        # Relocatable debug addresses can overlap between function sections. Do
        # not attribute another section's line table to this function by address.
        executable = [s for s in elf.iter_sections() if s['sh_flags'] & 4 and s['sh_size']]
        if len(executable) > 1:
            return []
        return _dwarf(elf, start, size, filename) or _dwarf1(elf, start, size, filename) or _ecoff(elf, data, start, size, name, filename)
    except (ELFError, DWARFError, ValueError, KeyError, IndexError, struct.error, TypeError, OSError):
        return []


def annotate(rows, offsets, spans, source, first_line=1):
    """Align only candidate instructions; target-only gaps have no invented C line."""
    lines, unmapped, candidate = {}, 0, 0
    for index, (marker, target, own) in enumerate(rows):
        offset = offsets[candidate] if own and candidate < len(offsets) else None
        if own:
            candidate += 1
        if marker == ' ':
            continue
        associated = {line - first_line + 1 for lo, hi, line in spans if offset is not None and lo <= offset < hi}
        associated = {line for line in associated if 1 <= line <= len(source.splitlines())}
        if len(associated) != 1:
            unmapped += 1
            continue
        line = associated.pop()
        severity = 'operand' if marker in ('r','s','i') else 'different'
        entry = lines.setdefault(line, {'line':line, 'severity':severity, 'instructions':[]})
        if severity == 'different':
            entry['severity'] = severity
        entry['instructions'].append({'row':index, 'marker':marker, 'target':target, 'compiled':own})
    return {'lines':sorted(lines.values(), key=lambda x:x['line']), 'unmapped':unmapped,
            'available':bool(spans), 'note':'Compiler line associations are advisory under optimization. Missing target-only instructions have no compiled C location.' if spans else
            'Source mapping unavailable: this object has no supported, unambiguous line information. Compiler settings are unchanged.'}


def mismatches(data, name, rows, source, filename='func.c', first_line=1, offsets=None):
    spans = locations(data, name, filename)
    if offsets is None:
        try:
            fn = extract_function(data, name)
            offsets = [ins.offset for ins in scoring.disassemble(fn)] if fn else []
        except (ELFError, ValueError, KeyError):
            offsets = []
    if len(offsets) != sum(bool(row[2]) for row in rows):
        spans = []  # A different instruction stream must not borrow this object's locations.
    return annotate(rows, offsets, spans, source, first_line)


def render(info, source, limit=8):
    """Bounded agent evidence using exactly the same map as the editor."""
    if not info['available']:
        return '\nC-line mapping unavailable (no supported debug line information; flags unchanged).'
    text = ['\nCompiled C lines associated with differences (advisory, not causes):']
    source_lines = source.splitlines()
    for entry in info['lines'][:limit]:
        text.append(f"  C line {entry['line']}: {source_lines[entry['line'] - 1].strip()[:160]} ({len(entry['instructions'])} differing instructions)")
        for ins in entry['instructions'][:2]:
            text.append(f"    {ins['marker']} target: {ins['target'][:120] or '(none)'} | compiled: {ins['compiled'][:120] or '(none)'}")
    if len(info['lines']) > limit:
        text.append(f"  {len(info['lines']) - limit} more mapped lines omitted.")
    if info['unmapped']:
        text.append(f"  {info['unmapped']} differences have no compiled C location (including target-only instructions).")
    return '\n'.join(text)


def feedback_note(text):
    """Retain bounded source locations when compact tool feedback replaces the assembly listing."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith('C-line mapping unavailable'):
            return '\n' + line
        if line.startswith('Compiled C lines associated with differences'):
            selected = [line]
            for following in lines[index + 1:]:
                if not following.startswith('  '):
                    break
                selected.append(following)
            return '\n' + '\n'.join(selected)
    return ''


def render_candidate(info, mapped_source, candidate, limit=4):
    """Translate a project function fragment to the exact submitted C, including preceding declarations."""
    if not info['available']:
        return render(info, candidate, limit=limit)
    fragment = mapped_source.rstrip()
    at = candidate.find(fragment) if fragment else -1
    if at < 0 or candidate.find(fragment, at + 1) >= 0:
        return '\nC-line mapping unavailable for this candidate source; no line locations inferred.'
    offset = candidate[:at].count('\n')
    mapped = {**info, 'lines': [{**line, 'line': line['line'] + offset} for line in info['lines']]}
    return render(mapped, candidate, limit=limit)
