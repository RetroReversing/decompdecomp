"""Prove saved native-module sources against newly recovered loader bindings."""

import json
import re

from ...app import overlay_donor_aliases
from ...app.function_names import rename_identifiers
from ...forks import csource
from ...network import NetworkError
from .uso_match import alias, local_alias


def _macro_variant(fn, source, header, candidate, info, extra):
    """Separate placeholder calls only when every occurrence has one recorded JAL."""
    span = csource.find(source, fn['name'])
    if span is None:
        return None
    clean = csource._blank(source)
    calls = list(re.finditer(r'\b(?:(?:CALL|FCALL)\s*\(\s*[A-Za-z_]\w*\s*\)|D_80000000(?=\s*\())',
                             clean[span[0]:span[1]]))
    calls = [(span[0] + match.start(), span[0] + match.end()) for match in calls]
    relocs = []
    target = fn['target']
    for at, kind, index in info.get('relocs') or ():
        if kind == 4 and 0 <= at and at + 4 <= len(target) and int.from_bytes(target[at:at + 4], 'big') >> 26 == 3:
            relocs.append((at, index))
    if not calls or len(calls) != len(relocs):
        return None
    actual = {(reloc.offset, reloc.type): reloc.symbol for reloc in candidate.relocs}
    if any(actual.get((at, 4)) != 'D_80000000' for at, _ in relocs):
        return None
    pattern = r'(?m)^[ \t]*extern[ \t]+[^;{}#\n]+\bD_80000000[ \t]*\([^;{}#\n]*\)[ \t]*;'
    declaration = re.search(pattern, csource._blank(header))
    if declaration is None:
        return None
    prototype = header[declaration.start():declaration.end()]
    local = {(at, index): offset for at, index, offset in info.get('local_calls', ())}
    bindings = [local_alias(info['section'], index, local[(at, index)]) if (at, index) in local
                else alias(info['section'], index) for at, index in sorted(relocs)]
    # The port build gives these call macros their original symbolic callees.
    # Keep each original expression there while the ROM build uses its binding.
    names = []
    preprocessor = csource._blank(header + '\n' + source)
    for index, ((start, end), binding) in enumerate(zip(calls, bindings), 1):
        name = f'decomp_overlay_call_{index}'
        if re.search(r'\b' + re.escape(name) + r'\b', preprocessor):
            return None
        names.append((name, source[start:end], binding))
    for (start, end), (name, _, _) in reversed(list(zip(calls, names))):
        source = source[:start] + name + source[end:]
    declarations = [rename_identifiers(prototype, {'D_80000000': binding}) for binding in sorted(set(bindings))]
    port = '\n'.join(f'#define {name} {original}' for name, original, _ in names)
    rom = '\n'.join(f'#define {name} {binding}' for name, _, binding in names)
    return '\n'.join(declarations) + '\n#ifdef PORT\n' + port + '\n#else\n' + rom + '\n#endif\n' + source + '\n' + extra


def _split_direct_data(fn, source, header, candidate):
    """Give individual placeholder data uses separate relocations for a private probe."""
    span = csource.find(source, fn['name'])
    if span is None:
        return None
    clean = csource._blank(source)
    body = clean[span[0]:span[1]]
    # XDATA can stringify its first argument in another build; leave those sites intact.
    if re.search(r'\bXDATA\s*\(', body):
        return None
    sites = list(re.finditer(r'\bD_00000000\b', body))
    sites = [(span[0] + m.start(), span[0] + m.end()) for m in sites]
    low = [r for r in candidate.relocs if r.type == 6 and r.symbol == 'D_00000000']
    if not sites or len(sites) != len(low):
        return None
    macros = set(re.findall(r'(?m)^\s*#\s*define\s+([A-Za-z_]\w*)\s*\(', csource._blank(header + '\n' + source)))
    for start, _ in sites:
        depth = 0
        for at in range(start - 1, span[0] - 1, -1):
            if clean[at] == ')':
                depth += 1
            elif clean[at] == '(':
                if depth:
                    depth -= 1
                    continue
                caller = re.search(r'([A-Za-z_]\w*)\s*$', clean[span[0]:at])
                if caller:
                    name = caller.group(1)
                    if name in macros or (name.isupper() and not re.fullmatch(r'D_[0-9A-Fa-f]{8}', name)):
                        return None
    pattern = r'(?m)^[ \t]*extern[ \t]+[^;{}#\n]+\bD_00000000[ \t]*;'
    declaration = re.search(pattern, csource._blank(header))
    if declaration is None:
        return None
    prototype = header[declaration.start():declaration.end()]
    names = [f'decomp_overlay_data_ref_{index}' for index in range(1, len(sites) + 1)]
    clean_all = csource._blank(header + '\n' + source)
    if any(re.search(r'\b' + re.escape(name) + r'\b', clean_all) for name in names):
        return None
    for (start, end), name in reversed(list(zip(sites, names))):
        source = source[:start] + name + source[end:]
    port = '\n'.join(f'#define {name} D_00000000' for name in names)
    rom = '\n'.join(rename_identifiers(prototype, {'D_00000000': name}) for name in names)
    return '#ifdef PORT\n' + port + '\n#else\n' + rom + '\n#endif\n' + source


def retry(net, project, fn, source):
    """Return a rewritten saved source only after its complete module-aware judge passes."""
    try:
        info = json.loads(fn['meta'] or '{}').get('uso') or {}
        if not info.get('native') or not isinstance(info.get('section'), int):
            return None
        unit = fn['unit'] or ''
        context = net._contexts(fn['project_id'], {unit}).get(unit)
        header = context['header'] if context else ''
        built = net._compile(project, header, source, fn)
        if not built.ok:
            return None
        candidate, exact, _ = net._evaluate(fn, project, built.obj)
        if candidate is None or exact:
            return None
        target = net._target_obj(fn, project)
        bound = overlay_donor_aliases.explicit_symbol_bindings(
            source, header, candidate, target, net.console(project).separate_overlay_reference)
        extra = bound[1][len(header):] if bound and bound[1].startswith(header) else ''
        variants = [source + '\n' + extra] if extra else []
        macro = _macro_variant(fn, source, header, candidate, info, extra)
        if macro:
            variants.append(macro)
        for variant in variants:
            built = net._compile(project, header, variant, fn)
            if built.ok and net._evaluate(fn, project, built.obj)[1] is True:
                return variant
        split = _split_direct_data(fn, source, header, candidate)
        if split:
            built = net._compile(project, header, split, fn)
            if not built.ok:
                return None
            separated, _, _ = net._evaluate(fn, project, built.obj)
            bound = overlay_donor_aliases.explicit_symbol_bindings(
                split, header, separated, target, net.console(project).separate_overlay_reference)
            if not bound or not bound[1].startswith(header):
                return None
            extra = bound[1][len(header):]
            variants = [split + '\n' + extra]
            macro = _macro_variant(fn, split, header, separated, info, extra)
            if macro:
                variants.append(macro)
            for variant in variants:
                built = net._compile(project, header, variant, fn)
                if built.ok and net._evaluate(fn, project, built.obj)[1] is True:
                    return variant
    except (NetworkError, OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None
    return None
