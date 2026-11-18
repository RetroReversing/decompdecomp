#!/usr/bin/env python3
"""Cut the game's functions out of your copy of the XEX2 into one ELF64 PPC64 object per source file, for objdiff.

    python tools/delink.py config/<version>/functions.json OUT_DIR

An Xbox 360 function is big-endian PowerPC64 code. A unit's object holds each function's bytes in its own section,
like the compiled candidate, with a RELA relocation for every reference that leaves it (a call, a TOC-relative
address, an absolute immediate). Relocation fields are zeroed and their addends live in the RELA entries; the
destination symbol and addend remain part of a strict byte comparison.

This emits *structurally correct* ELF64 ET_REL PPC64 objects: the headers, sections, symbols and RELA entries are
what a 64-bit big-endian PowerPC linker tool expects. It does not compile or judge anything: objdiff still needs the
user's xsdk kit to compile each candidate source into a comparable object. Stage 3 records functions with no
recovered relocations, so the empty-relocs case is the common one; the relocation path is for map producers that
add them. Standard library only. Made by decompdecomp.
"""

import hashlib
import json
import struct
import sys
from pathlib import Path

# PPC64 relocation numbers, from src/decompnet/arch/ppc64.py. The masks below are a local copy of that module's
# RELOC_MASKS (relocation type -> (byte offset from r_offset, field length, big-endian bits to clear)); duplicating
# them keeps this tool standalone, since a generated project must not import decompnet.
R_PPC64_ADDR32, R_PPC64_ADDR16, R_PPC64_ADDR16_LO, R_PPC64_ADDR16_HI, R_PPC64_ADDR16_HA = 1, 3, 4, 5, 6
R_PPC64_REL24, R_PPC64_REL32, R_PPC64_ADDR64 = 10, 26, 38
R_PPC64_TOC16, R_PPC64_TOC16_LO, R_PPC64_TOC16_HI, R_PPC64_TOC16_HA, R_PPC64_TOC = 47, 48, 49, 50, 51
R_PPC64_ADDR16_DS, R_PPC64_ADDR16_LO_DS, R_PPC64_TOC16_DS, R_PPC64_TOC16_LO_DS = 56, 57, 63, 64
R_SLOT_CONSTANT = 1000  # not a real relocation: a load of a constant the TOC itself holds

HALF = 0xFFFF
RELOC_MASKS = {
    R_PPC64_ADDR32: (0, 4, 0xFFFFFFFF),
    R_PPC64_ADDR16: (0, 2, HALF), R_PPC64_ADDR16_LO: (0, 2, HALF), R_PPC64_ADDR16_HI: (0, 2, HALF),
    R_PPC64_ADDR16_HA: (0, 2, HALF),
    R_PPC64_REL24: (0, 4, 0x03FFFFFC),
    R_PPC64_REL32: (0, 4, 0xFFFFFFFF),
    R_PPC64_ADDR64: (0, 8, 0xFFFFFFFFFFFFFFFF),
    R_PPC64_TOC16: (0, 2, HALF), R_PPC64_TOC16_LO: (0, 2, HALF), R_PPC64_TOC16_HI: (0, 2, HALF),
    R_PPC64_TOC16_HA: (0, 2, HALF), R_PPC64_TOC: (0, 8, 0xFFFFFFFFFFFFFFFF), R_SLOT_CONSTANT: (0, 2, HALF),
    R_PPC64_ADDR16_DS: (0, 2, 0xFFFC), R_PPC64_ADDR16_LO_DS: (0, 2, 0xFFFC),
    R_PPC64_TOC16_DS: (0, 2, 0xFFFC), R_PPC64_TOC16_LO_DS: (0, 2, 0xFFFC),
}
RELOC_WIDTH = {rtype: mask[1] for rtype, mask in RELOC_MASKS.items()}

ET_REL, EM_PPC64, SHT_RELA = 1, 21, 4
SHF_ALLOC, SHF_EXECINSTR, SHF_INFO_LINK = 0x2, 0x4, 0x40
STB_LOCAL, STB_GLOBAL, STT_NOTYPE, STT_FUNC = 0, 1, 0, 2
EMPTY = 0


def _align(data: bytearray, n: int) -> None:
    while len(data) % n:
        data.append(0)


def _section(name: int, stype: int, flags: int, addr: int, offset: int, size: int, link: int = 0, info: int = 0,
             addralign: int = 1, entsize: int = 0) -> bytes:
    return struct.pack(">IIQQQQIIQQ", name, stype, flags, addr, offset, size, link, info, addralign, entsize)


def elf64(unit: str, functions: list[dict], blob: bytes, start: int) -> bytes:
    """One independently relocatable text section per function, like a -ffunction-sections candidate.

    Inter-function gaps are alignment, not instructions of either function. Original addresses are kept in a
    non-allocated metadata section for explicit guard binding; symbol values remain section-relative ELF values.
    Every header and field is big-endian: the Xbox 360's PowerPC64 is MSB-first.
    """
    symbols = []
    externs = {}
    addresses = {}
    for index, fn in enumerate(functions):
        if fn["name"] in externs:
            raise ValueError(f"duplicate function symbol in {unit}: {fn['name']}")
        externs[fn["name"]] = len(symbols) + 1  # index zero is the ELF null symbol
        symbols.append((fn["name"], (STB_GLOBAL << 4) | STT_FUNC, 1 + index * 2, 0, fn["size"]))
        addresses[fn["name"]] = fn["addr"]

    sym_index = 2 * len(functions) + 2  # null, text/rela pairs, original-address metadata, then symbol table
    sections = []
    for index, fn in enumerate(functions):
        at, size = fn["addr"] - start, fn["size"]
        if at < 0 or size <= 0 or at + size > len(blob):
            raise ValueError(f"invalid function extent in {unit}: {fn['name']}")
        text = bytearray(blob[at:at + size])
        relocs = bytearray()
        for off, rtype, name, addend in fn.get("relocs") or []:
            if not name:
                continue
            width = RELOC_WIDTH.get(rtype)
            if width is None or off < 0 or off + width > size:
                raise ValueError(f"invalid relocation field in {fn['name']} at {off}: type {rtype}")
            text[off:off + width] = bytes(width)  # big-endian: the field is a run of zero bytes either way
            if name not in externs:
                externs[name] = len(symbols) + 1
                symbols.append((name, (STB_GLOBAL << 4) | STT_NOTYPE, 0, 0, 0))
            relocs += struct.pack(">QQq", off, (externs[name] << 32) | rtype, addend)
        section = ".text" if index == 0 else f".text.{fn['name']}"
        sections.append((section, 1, SHF_ALLOC | SHF_EXECINSTR, bytes(text), 0, 0, 16, 0))
        sections.append((f".rela{section}", SHT_RELA, SHF_INFO_LINK, bytes(relocs), sym_index, 1 + index * 2, 8, 24))

    strtab = bytearray(b"\0")
    table = bytearray(24)
    for name, info, shndx, value, size in symbols:
        table += struct.pack(">IBBHQQ", len(strtab), info, 0, shndx, value, size)
        strtab += name.encode("utf-8") + b"\0"
    sections.extend([
        (".decomp.addresses", 1, 0, json.dumps(addresses, sort_keys=True).encode("utf-8"), 0, 0, 1, 0),
        (".symtab", 2, 0, bytes(table), sym_index + 1, 1, 8, 24),
        (".strtab", 3, 0, bytes(strtab), 0, 0, 1, 0),
    ])
    shstr = bytearray(b"\0")
    names = {}
    for name in [*(s[0] for s in sections), ".shstrtab"]:
        names[name] = len(shstr)
        shstr += name.encode("utf-8") + b"\0"
    sections.append((".shstrtab", 3, 0, bytes(shstr), 0, 0, 1, 0))

    out = bytearray(64)
    headers = [_section(0, EMPTY, 0, 0, 0, 0)]
    for name, kind, flags, data, link, info, alignment, entsize in sections:
        _align(out, alignment)
        headers.append(_section(names[name], kind, flags, 0, len(out), len(data), link, info, alignment, entsize))
        out += data
    _align(out, 8)
    sh_at = len(out)
    out += b"".join(headers)
    ident = b"\x7fELF" + bytes([2, 2, 1, 0]) + bytes(8)  # ELFCLASS64, ELFDATA2MSB, EV_CURRENT, SYSV ABI
    struct.pack_into(">16sHHIQQQIHHHHHH", out, 0, ident, ET_REL, EM_PPC64, 1, 0, 0, sh_at, 0, 64,
                     0, 0, 64, len(headers), len(headers) - 1)
    return bytes(out)


def main() -> None:
    config, out = Path(sys.argv[1]), Path(sys.argv[2])
    info = json.loads(config.read_text(encoding="utf-8"))
    data = Path(info["elf"]).read_bytes()
    if hashlib.sha1(data).hexdigest() != info["sha1"]:
        sys.exit(f"{info['elf']} isn't the copy this project was made from (its SHA-1 should be {info['sha1']})")
    out.mkdir(parents=True, exist_ok=True)
    for unit, functions in info["units"].items():
        start = functions[0]["addr"]
        end = max(fn["addr"] + fn["size"] for fn in functions)
        blob = data[functions[0]["offset"]:functions[0]["offset"] + (end - start)]
        obj = elf64(unit, functions, blob, start)
        path = out / f"{unit}.o"
        if not path.exists() or path.read_bytes() != obj:  # unchanged objects keep their time: nothing rebuilds
            path.write_bytes(obj)


if __name__ == "__main__":
    main()
