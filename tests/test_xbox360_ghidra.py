"""Stage 4a Xbox 360: Ghidra discovery through a disposable ELF64/PPC64 wrapper, without Ghidra installed.

The live Ghidra analysis cannot be verified here: Ghidra is not installed in this environment. These tests parse the
wrapper's bytes and stub the shared ``ghidra.functions`` call, so they cover the wrapper and the VA->file-offset
conversion only. They never exercise real Ghidra analysis or claim that it works.
"""

import json
import struct
from pathlib import Path

import pytest

from decompnet.consoles.xbox360 import XBOX360, scratch
from decompnet.consoles.xbox360.scratch import _function_from_ghidra
from decompnet.forks.project import ForkError

BASE = 0x80000000
ENTRY = 0x80000100
PAYLOAD = 0x1000
SECURITY = 0x200
IMAGE_SIZE = 0x400

EHDR = ">16sHHIQQQIHHHHHH"  # 64-byte ELF64 header
PHDR = ">IIQQQQQQ"          # 56-byte ELF64 program header


def _image(n: int = IMAGE_SIZE) -> bytes:
    return bytes((i * 7 + 3) & 0xFF for i in range(n))


def _xex2(image: bytes, *, entry: int | None = ENTRY) -> bytes:
    """A tiny, self-consistent XEX2 header plus its image bytes."""
    size = len(image)
    descriptors = []
    if entry is not None:
        descriptors.append((0x10100, entry))
    descriptors.append((0x10201, BASE))
    buf = bytearray(PAYLOAD + size)
    buf[0:4] = b"XEX2"
    buf[8:12] = struct.pack(">I", PAYLOAD)
    buf[16:20] = struct.pack(">I", SECURITY)
    buf[20:24] = struct.pack(">I", len(descriptors))
    at = 24
    for key, value in descriptors:
        buf[at:at + 8] = struct.pack(">II", key, value)
        at += 8
    buf[SECURITY:SECURITY + 4] = struct.pack(">I", 0x40)
    buf[SECURITY + 4:SECURITY + 8] = struct.pack(">I", size)
    buf[PAYLOAD:PAYLOAD + size] = image
    return bytes(buf)


def _game(tmp_path: Path, image: bytes, *, entry: int | None = ENTRY, name: str = "MyGame.xex") -> Path:
    game = tmp_path / name
    game.write_bytes(_xex2(image, entry=entry))
    return game


def _header(data: bytes) -> dict:
    fields = struct.unpack_from(EHDR, data, 0)
    names = ("ident", "type", "machine", "version", "entry", "phoff", "shoff", "flags",
             "ehsize", "phentsize", "phnum", "shentsize", "shnum", "shstrndx")
    return dict(zip(names, fields))


def test_ghidra_image_is_a_big_endian_ppc64_executable(tmp_path: Path):
    image = _image()
    game = _game(tmp_path, image)
    xex = scratch.read_xex2(game.read_bytes())

    wrapper = scratch.ghidra_image(game, xex, tmp_path)
    data = wrapper.read_bytes()
    h = _header(data)
    assert h["ident"][:6] == b"\x7fELF\x02\x02"  # ELFCLASS64, ELFDATA2MSB
    assert h["type"] == 2 and h["machine"] == 21 and h["version"] == 1  # ET_EXEC, EM_PPC64
    assert h["entry"] == ENTRY
    assert h["phoff"] == 64 and h["phentsize"] == 56 and h["phnum"] == 1
    assert h["ehsize"] == 64 and h["shoff"] == 0 and h["shnum"] == 0  # no section headers

    p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_align = struct.unpack_from(PHDR, data, h["phoff"])
    assert p_type == 1 and p_flags & 0x1  # PT_LOAD, executable
    assert p_vaddr == BASE and p_paddr == BASE
    assert p_offset == h["phoff"] + h["phentsize"] == 120
    assert p_filesz == p_memsz == IMAGE_SIZE
    assert p_align == 1  # no alignment: p_offset need not match image_base modulo a page size
    assert data[p_offset:p_offset + p_filesz] == image
    assert len(data) == p_offset + IMAGE_SIZE


def test_ghidra_image_uses_image_base_without_an_entry_point(tmp_path: Path):
    game = _game(tmp_path, _image(0x100), entry=None)
    xex = scratch.read_xex2(game.read_bytes())
    assert xex["entry_point"] is None
    assert _header(scratch.ghidra_image(game, xex, tmp_path).read_bytes())["entry"] == BASE


def test_ghidra_image_rejects_a_truncated_image(tmp_path: Path):
    game = tmp_path / "short.xex"
    game.write_bytes(_xex2(_image())[:PAYLOAD + 8])  # header parses, image bytes are missing
    xex = scratch.read_xex2(game.read_bytes())
    with pytest.raises(ForkError, match="truncated"):
        scratch.ghidra_image(game, xex, tmp_path)


def test_ghidra_functions_requires_ghidra(tmp_path: Path, monkeypatch):
    from decompnet.forks import ghidra

    monkeypatch.setattr(ghidra, "available", lambda: False)
    monkeypatch.setattr(ghidra, "functions", lambda _program: pytest.fail("no Ghidra call when unavailable"))

    game = _game(tmp_path, _image())
    xex = scratch.read_xex2(game.read_bytes())
    with pytest.raises(ForkError, match="Ghidra isn't installed"):
        scratch.ghidra_functions(game, xex)


def test_function_from_ghidra_converts_vas_and_drops_bad_rows():
    xex = {"image_base": BASE, "payload_offset": PAYLOAD, "image_size": IMAGE_SIZE}

    got = _function_from_ghidra({"entry": BASE + 0x40, "end": BASE + 0x50, "name": "foo"}, xex)
    assert got == {"name": "foo", "addr": BASE + 0x40, "offset": PAYLOAD + 0x40, "size": 0x10,
                   "ranges": [[BASE + 0x40, BASE + 0x50]]}

    # Ghidra's JSON uses hex strings for VAs; they convert the same way as ints
    got = _function_from_ghidra({"entry": f"{BASE + 0x200:x}", "end": f"{BASE + 0x210:x}", "name": ""}, xex)
    assert got == {"name": "", "addr": BASE + 0x200, "offset": PAYLOAD + 0x200, "size": 0x10,
                   "ranges": [[BASE + 0x200, BASE + 0x210]]}

    # thunks, empty extents, addresses below the image and extents past it are all dropped
    assert _function_from_ghidra({"entry": BASE, "end": BASE + 0x10, "thunk": True}, xex) is None
    assert _function_from_ghidra({"entry": BASE, "end": BASE}, xex) is None
    assert _function_from_ghidra({"entry": BASE - 0x10, "end": BASE}, xex) is None
    assert _function_from_ghidra({"entry": BASE + IMAGE_SIZE - 4, "end": BASE + IMAGE_SIZE + 4}, xex) is None
    assert _function_from_ghidra({}, xex) is None


def test_ghidra_functions_converts_stubbed_rows(tmp_path: Path, monkeypatch):
    from decompnet.forks import ghidra

    seen = {}

    def _fake_functions(program: Path, *, require_conventions: bool = True):
        data = program.read_bytes()  # the wrapper is still alive inside the temp dir
        seen["elf"] = data[:6]
        seen["name"] = program.name
        seen["require_conventions"] = require_conventions
        return [
            {"entry": BASE, "end": BASE + 0x20, "name": "start", "thunk": False},
            {"entry": BASE + 0x40, "end": BASE + 0x50, "name": "stub", "thunk": True},
            {"entry": BASE + 0x1000, "end": BASE + 0x1010, "name": "outside", "thunk": False},
        ]

    monkeypatch.setattr(ghidra, "available", lambda: True)
    monkeypatch.setattr(ghidra, "functions", _fake_functions)
    game = _game(tmp_path, _image())
    xex = scratch.read_xex2(game.read_bytes())

    got = scratch.ghidra_functions(game, xex)
    assert seen["elf"] == b"\x7fELF\x02\x02" and seen["name"] == "xex2-ppc64.elf"
    assert seen["require_conventions"] is False  # PPC64 has a fixed ABI; unknown conventions are fine
    assert got == [
        {"name": "start", "addr": BASE, "offset": PAYLOAD, "size": 0x20, "ranges": [[BASE, BASE + 0x20]]},
    ]


def test_create_uses_the_map_path_without_ghidra(tmp_path: Path, monkeypatch):
    from decompnet.forks import ghidra

    monkeypatch.setattr(ghidra, "available", lambda: pytest.fail("no Ghidra needed with a function map"))
    monkeypatch.setattr(ghidra, "functions", lambda _program: pytest.fail("no Ghidra call with a map"))

    game = _game(tmp_path, _image())
    (tmp_path / "functions.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in [{"a": f"{BASE:x}", "end": f"{BASE + 0x10:x}", "n": "entry"}]),
        encoding="utf-8")

    root = tmp_path / "project"
    info = XBOX360.create_project(root, game)
    assert info["platform"] == "xbox360" and info["sources"] == 1
    cfg = json.loads((root / "config" / info["id"] / "functions.json").read_text(encoding="utf-8"))
    fn = next(iter(cfg["units"].values()))[0]
    assert fn["name"] == "entry" and fn["addr"] == BASE and fn["offset"] == PAYLOAD
    src = next((root / "src" / "auto").glob("*.c")).read_text(encoding="utf-8")
    assert "from the game's function map" in src


def test_create_without_a_map_or_ghidra_has_an_actionable_error(tmp_path: Path, monkeypatch):
    from decompnet.forks import ghidra

    monkeypatch.setattr(ghidra, "available", lambda: False)
    game = _game(tmp_path, _image())
    with pytest.raises(ForkError, match="no Ghidra"):
        XBOX360.create_project(tmp_path / "project", game)
    assert not (tmp_path / "project").exists()
