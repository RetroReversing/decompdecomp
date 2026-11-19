"""An Xbox 360 image's own function table: the PE ``.pdata`` section, read as the loader reads it.

Every function the compiler emitted unwind data for has an 8-byte entry there: its start address, then a packed word
whose bits 8-29 are its length in instructions (bits 0-7 the prologue length, 30 a 32-bit flag, 31 an exception
flag). It is the compiler's own account of where functions begin and end, so it is better evidence than any
analysis: on an Xbox 360 game where Ghidra found 18,819 functions, ``.pdata`` lists 27,923 (99% of them beginning
with the standard prologue), and gave a different length for 7,776 of the starts both agreed on.

It also tells a base image's layout apart. XexTool writes ``xextool -b`` images in MEMORY layout (each section
already at its virtual address) while their section table still gives file offsets; mapped as a file layout, a
game's ``.text`` landed 0x5E00 bytes from its address and every target object was cut from the wrong bytes.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MFLR_R12 = b"\x7d\x88\x02\xa6"  # mflr r12: how almost every non-leaf Xbox 360 function begins
EXECUTABLE = 0x20000000  # IMAGE_SCN_MEM_EXECUTE
SAMPLE = 4000  # entries looked at when judging a table or a layout: enough to be sure, cheap on 100k-entry tables
PROLOGUE_SHARE = 0.3  # below this share of prologue starts, the table doesn't describe these bytes


@dataclass(frozen=True)
class Section:
    name: str
    va: int  # relative to the image base
    vsize: int
    raw: int  # the file offset the section table gives
    rsize: int
    flags: int

    @property
    def executable(self) -> bool:
        return bool(self.flags & EXECUTABLE)


def header(data: bytes) -> tuple[int, int, int, list[Section]] | None:
    """(image base, SizeOfImage, SizeOfHeaders, sections) from a PE header at the start of ``data``, or None."""
    try:
        if data[:2] != b"MZ":
            return None
        lfanew = struct.unpack_from("<I", data, 0x3C)[0]
        if data[lfanew:lfanew + 4] != b"PE\0\0":
            return None
        count, optsize = struct.unpack_from("<H", data, lfanew + 6)[0], struct.unpack_from("<H", data, lfanew + 20)[0]
        opt = lfanew + 24
        base = struct.unpack_from("<I", data, opt + 28)[0]
        image_size, header_size = struct.unpack_from("<II", data, opt + 56)
        if count > 96:
            return None
        sections = []
        for index in range(count):
            at = opt + optsize + index * 40
            name = data[at:at + 8].rstrip(b"\0").decode("latin-1")
            vsize, va, rsize, raw = struct.unpack_from("<IIII", data, at + 8)
            sections.append(Section(name, va, vsize, raw, rsize, struct.unpack_from("<I", data, at + 36)[0]))
    except struct.error:
        return None
    return base, image_size, header_size, sections


def entries(table: bytes) -> list[tuple[int, int]]:
    """(start VA, length in bytes) for each non-empty entry of a ``.pdata`` table."""
    out = []
    for at in range(0, len(table) - 7, 8):
        begin, packed = struct.unpack_from(">II", table, at)
        length = ((packed >> 8) & 0x3FFFFF) * 4
        if begin and length:
            out.append((begin, length))
    return out


def _prologue_share(data: bytes, starts: list[int], offset_of) -> float:
    """The share of ``starts`` whose first or second instruction, read at ``offset_of(start)``, is mflr r12."""
    sample = starts[:: max(1, len(starts) // SAMPLE)][:SAMPLE]
    if not sample:
        return 0.0
    hits = 0
    for start in sample:
        at = offset_of(start)
        if 0 <= at and (data[at:at + 4] == MFLR_R12 or data[at + 4:at + 8] == MFLR_R12):
            hits += 1
    return hits / len(sample)


def _section_of(sections: list[Section], name: str) -> Section | None:
    return next((s for s in sections if s.name == name), None)


def memory_layout(data: bytes) -> bool:
    """Whether a PE base image is already in memory layout (sections at their virtual addresses), not file layout.

    The ``.pdata`` table is read both ways, and the reading that puts its function starts on prologues wins. A file
    with no table to judge by, or whose sections sit at the same place either way, is file layout (the PE default).
    """
    parsed = header(data)
    if parsed is None:
        return False
    base, _, _, sections = parsed
    table = _section_of(sections, ".pdata")
    code = [s for s in sections if s.executable]
    if table is None or not code or all(s.va == s.raw for s in sections):
        return False

    def file_offset(va: int) -> int:  # an address read in file layout: through its section's raw offset
        rva = va - base
        hit = next((s for s in sections if s.va <= rva < s.va + max(s.vsize, s.rsize)), None)
        return -1 if hit is None else rva - hit.va + hit.raw

    as_memory = [b for b, _ in entries(data[table.va:table.va + table.vsize])]
    as_file = [b for b, _ in entries(data[table.raw:table.raw + table.vsize])]
    memory = _prologue_share(data, as_memory, lambda va: va - base)
    on_file = _prologue_share(data, as_file, file_offset)
    return memory > on_file and memory >= PROLOGUE_SHARE


def functions(image: bytes, image_base: int, *, log=print) -> list[tuple[int, int]] | None:
    """The functions an image's ``.pdata`` lists, as sorted, non-overlapping (start VA, length) pairs, or None.

    ``image`` is the mapped image (offset 0 is ``image_base``, its PE header at the start). None when there is no
    header or table, or when the table's starts don't land on code: an image whose sections aren't at their
    addresses (a mis-mapped base image) gives a table that describes other bytes, and its "functions" would be cut
    from the wrong places, so the caller falls back to analysis rather than trust it.
    """
    parsed = header(image)
    if parsed is None:
        return None
    base, _, _, sections = parsed
    table = _section_of(sections, ".pdata")
    if base != image_base or table is None:
        return None
    code = [(base + s.va, base + s.va + max(s.vsize, s.rsize)) for s in sections if s.executable]
    found: list[tuple[int, int]] = []
    end = 0
    for begin, length in sorted(entries(image[table.va:table.va + table.vsize])):
        if begin < end or not any(lo <= begin and begin + length <= hi for lo, hi in code):
            continue  # an overlap or an extent outside the code: not one function of these bytes
        found.append((begin, length))
        end = begin + length
    if not found:
        return None
    share = _prologue_share(image, [b for b, _ in found], lambda va: va - base)
    if share < PROLOGUE_SHARE:
        log(f"the image's function table (.pdata) doesn't match its code ({share:.0%} of its functions begin with a "
            "prologue): not using it")
        return None
    log(f"the image's function table (.pdata) lists {len(found)} functions ({share:.0%} begin with a prologue)")
    return found


def merge(table: list[dict], analysed: list[dict] | None) -> tuple[list[dict], int, int]:
    """``.pdata``'s functions, with what analysis adds: (functions, gaps filled, fragments dropped).

    The table decides where functions start and end. An analysed function at the same start lends its name; one
    that lies outside every table function fills a gap (a leaf with no unwind data); one inside a table function is
    a fragment of it (Ghidra cut one game's functions into 14,000 eight-byte pieces) and is dropped.
    """
    if not analysed:
        return list(table), 0, 0
    by_start = {row["addr"]: row for row in table}
    starts = sorted(by_start)
    import bisect

    out = list(table)
    added = dropped = 0
    for row in analysed:
        same = by_start.get(row["addr"])
        if same is not None:
            name = row.get("name") or ""
            if name and not name.upper().startswith(("FUN_", "FUNC_")):
                same["name"] = name  # a real name, not an address
            continue
        end = row["addr"] + row["size"]
        index = bisect.bisect_right(starts, row["addr"]) - 1
        before = by_start[starts[index]] if index >= 0 else None
        after = starts[index + 1] if index + 1 < len(starts) else None
        if (before is not None and row["addr"] < before["addr"] + before["size"]) or (after is not None and end > after):
            dropped += 1
            continue
        out.append(row)
        added += 1
    out.sort(key=lambda row: row["addr"])
    return out, added, dropped
