"""The Xbox 360 project template's PPC64 delinker: struct, symbols, RELA and the build rule that drives it.

No xsdk kit, Ghidra or objdiff needed: these build a synthetic big-endian PPC64 blob and read the ELF64 the tool
writes back with struct. The objects are structurally correct; whether a candidate byte-matches is objdiff's job.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "src" / "decompnet" / "consoles" / "xbox360" / "project_template" / "tools"

_spec = importlib.util.spec_from_file_location("xbox360_delink_test", TOOLS / "delink.py")
delink = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(delink)

ADDR32, ADDR64, REL24 = 1, 38, 10  # the PPC64 relocation numbers the tool copies

# Four-word instructions, big-endian: the fake "game" bytes the functions are cut from.
_WORDS = [0x60000000 + i for i in range(16)]
BLOB = b"".join(w.to_bytes(4, "big") for w in _WORDS)  # 64 bytes

FNS = [
    {"name": "fn_a", "addr": 0x1000, "offset": 0, "size": 16, "relocs": []},
    {"name": "fn_b", "addr": 0x1020, "offset": 0x20, "size": 8, "relocs": []},
]


def _sections(obj: bytes) -> list[dict]:
    (_, e_type, e_machine, _, _, _, shoff, _, _, _, _, shentsize, shnum, shstrndx) = struct.unpack_from(
        ">16sHHIQQQIHHHHHH", obj, 0)
    raw = []
    for i in range(shnum):
        (name, stype, flags, addr, offset, size, link, info, align, entsize) = struct.unpack_from(
            ">IIQQQQIIQQ", obj, shoff + i * shentsize)
        raw.append(dict(name=name, type=stype, flags=flags, addr=addr, offset=offset, size=size,
                        link=link, info=info, align=align, entsize=entsize))
    shstr = raw[shstrndx]
    strs = obj[shstr["offset"]:shstr["offset"] + shstr["size"]]
    for s in raw:
        s["sname"] = strs[s["name"]:strs.find(b"\0", s["name"])].decode()
        s["data"] = obj[s["offset"]:s["offset"] + s["size"]]
    return raw


def _by_name(obj: bytes) -> dict[str, dict]:
    return {s["sname"]: s for s in _sections(obj)}


def _symbols(obj: bytes) -> list[dict]:
    secs = _by_name(obj)
    symtab, strtab = secs[".symtab"]["data"], secs[".strtab"]["data"]
    out = []
    for at in range(0, len(symtab), 24):
        name, info, other, shndx, value, size = struct.unpack_from(">IBBHQQ", symtab, at)
        out.append(dict(name=strtab[name:strtab.find(b"\0", name)].decode(), info=info, shndx=shndx,
                        value=value, size=size))
    return out


def _relocs(obj: bytes, section: str) -> list[tuple[int, int, int]]:
    data = _by_name(obj)[section]["data"]
    return [struct.unpack_from(">QQq", data, at) for at in range(0, len(data), 24)]


def test_elf64_header_is_big_endian_ppc64_relocatable():
    obj = delink.elf64("unit", FNS, BLOB, 0x1000)
    assert obj[:4] == b"\x7fELF"
    assert obj[4] == 2  # EI_CLASS: ELFCLASS64
    assert obj[5] == 2  # EI_DATA: ELFDATA2MSB, not ELFDATA2LSB
    assert obj[6] == 1  # EI_VERSION: EV_CURRENT
    e_type, e_machine = struct.unpack_from(">HH", obj, 16)
    assert (e_type, e_machine) == (1, 21)  # ET_REL, EM_PPC64


def test_section_layout_keeps_each_function_in_its_own_text_section():
    obj = delink.elf64("unit", FNS, BLOB, 0x1000)
    secs = _sections(obj)
    assert [s["sname"] for s in secs] == [
        "", ".text", ".rela.text", ".text.fn_b", ".rela.text.fn_b",
        ".decomp.addresses", ".symtab", ".strtab", ".shstrtab",
    ]
    by = _by_name(obj)
    assert by[".text"]["data"] == BLOB[:16]
    assert by[".text.fn_b"]["data"] == BLOB[0x20:0x28]
    assert by[".text"]["flags"] & 0x4  # SHF_EXECINSTR
    assert by[".rela.text"]["type"] == 4  # SHT_RELA
    assert by[".decomp.addresses"]["data"] == json.dumps(
        {"fn_a": 0x1000, "fn_b": 0x1020}, sort_keys=True).encode()
    assert _relocs(obj, ".rela.text") == []
    assert _relocs(obj, ".rela.text.fn_b") == []
    assert [s["name"] for s in _symbols(obj)] == ["", "fn_a", "fn_b"]


def test_external_relocation_is_a_big_endian_rela_with_a_zeroed_field():
    code = bytearray(BLOB)
    code[4:8] = (0xDEADBEEF).to_bytes(4, "big")
    fns = [dict(FNS[0], relocs=[[4, ADDR32, "extern_sym", 0x1234]]), FNS[1]]
    obj = delink.elf64("unit", fns, bytes(code), 0x1000)
    by = _by_name(obj)
    assert by[".text"]["data"][4:8] == b"\0\0\0\0"  # the patched field is zeroed in the section
    assert by[".text"]["data"][:4] == BLOB[:4]  # everything else is untouched
    syms = _symbols(obj)
    assert [s["name"] for s in syms] == ["", "fn_a", "fn_b", "extern_sym"]
    assert syms[3]["info"] == (1 << 4) | 0  # STB_GLOBAL | STT_NOTYPE
    assert syms[3]["shndx"] == 0  # an external symbol is undefined here
    sym = next(i for i, s in enumerate(syms) if s["name"] == "extern_sym")
    assert _relocs(obj, ".rela.text") == [(4, (sym << 32) | ADDR32, 0x1234)]
    assert _relocs(obj, ".rela.text.fn_b") == []


def test_relocation_widths_cover_the_ppc64_field_sizes():
    assert delink.RELOC_WIDTH[ADDR32] == 4
    assert delink.RELOC_WIDTH[ADDR64] == 8
    assert delink.RELOC_WIDTH[REL24] == 4
    for rtype in (3, 4, 5, 6, 47, 48, 49, 50, 56, 57, 63, 64, 1000):
        assert delink.RELOC_WIDTH[rtype] == 2
    assert delink.RELOC_WIDTH[51] == 8  # R_PPC64_TOC


def test_invalid_extents_duplicate_names_and_relocs_are_rejected():
    with pytest.raises(ValueError, match="duplicate function symbol"):
        delink.elf64("unit", [FNS[0], dict(FNS[1], name="fn_a")], BLOB, 0x1000)
    with pytest.raises(ValueError, match="invalid function extent"):
        delink.elf64("unit", [dict(FNS[0], size=0)], BLOB, 0x1000)
    with pytest.raises(ValueError, match="invalid function extent"):
        delink.elf64("unit", [dict(FNS[0], size=0x100)], BLOB, 0x1000)
    with pytest.raises(ValueError, match="invalid relocation field"):
        delink.elf64("unit", [dict(FNS[0], relocs=[[0, 9999, "x", 0]])], BLOB, 0x1000)
    with pytest.raises(ValueError, match="invalid relocation field"):
        delink.elf64("unit", [dict(FNS[0], relocs=[[14, ADDR32, "x", 0]])], BLOB, 0x1000)
    with pytest.raises(ValueError, match="invalid relocation field"):
        delink.elf64("unit", [dict(FNS[0], relocs=[[-1, ADDR32, "x", 0]])], BLOB, 0x1000)


def test_main_delinks_writes_objs_checks_sha1_and_keeps_unchanged_objects(tmp_path):
    config = tmp_path / "config" / "v"
    config.mkdir(parents=True)
    orig = tmp_path / "orig" / "game.xex"
    orig.parent.mkdir()
    orig.write_bytes(BLOB)
    info = {"elf": str(orig), "sha1": hashlib.sha1(BLOB).hexdigest(),
            "units": {"main": FNS}}
    (config / "functions.json").write_text(json.dumps(info))
    out = tmp_path / "build" / "v" / "target"
    cmd = [sys.executable, str(TOOLS / "delink.py"), str(config / "functions.json"), str(out)]

    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    obj_path = out / "main.o"
    assert obj_path.is_file()
    obj = obj_path.read_bytes()
    assert obj[:6] == b"\x7fELF\x02\x02"
    assert struct.unpack_from(">H", obj, 18)[0] == 21

    # A rerun over identical bytes must not rewrite the object: parallel builds rely on its timestamp.
    fixed = 1_600_000_000
    os.utime(obj_path, (fixed, fixed))
    assert subprocess.run(cmd, capture_output=True, text=True).returncode == 0
    assert os.stat(obj_path).st_mtime_ns == fixed * 1_000_000_000

    # A different copy of the game is refused.
    orig.write_bytes(BLOB + b"\0")
    bad = subprocess.run(cmd, capture_output=True, text=True)
    assert bad.returncode != 0
    assert "SHA-1" in bad.stderr


def test_configure_emits_a_delink_step_feeding_objdiff_targets(tmp_path):
    template = tmp_path / "project_template"
    shutil.copytree(TOOLS, template / "tools")
    config = template / "config" / "v"
    config.mkdir(parents=True)
    (config / "functions.json").write_text(json.dumps({"units": {"main": FNS}}))
    run = subprocess.run([sys.executable, "tools/configure.py", "v"], cwd=template,
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    ninja = (template / "build.ninja").read_text()
    assert "rule delink" in ninja
    assert "tools/delink.py" in ninja
    assert "build/v/target/main.o" in ninja
    objdiff = json.loads((template / "objdiff.json").read_text())
    assert objdiff["units"][0]["target_path"] == "build/v/target/main.o"
