"""Two Xbox 360 import regressions: PowerPC64 needs no calling conventions from Ghidra, and a basic-compressed XEX2
must be judged against its expanded image, not the compressed bytes at ``payload_offset``.

No real Ghidra here: the decompiler guard is exercised by stubbing ``ghidra._run_script``/``ghidra.functions``, and
the project is created from a synthetic function map.
"""

import hashlib
import json
import struct
from pathlib import Path

import pytest

from decompnet.consoles.xbox360 import XBOX360, scratch
from decompnet.forks import ghidra
from decompnet.forks.project import ForkError

BASE = 0x80000000
ENTRY = 0x80000100
PAYLOAD = 0x1000
SECURITY = 0x200
FMT_OFFSET = 0x100


def _xex2(image: bytes, blocks: list[tuple[int, int]] | None = None) -> bytes:
    """A tiny XEX2: raw ``image`` at the payload, or basic-compressed ``blocks`` (data_size, zero_size) pairs."""
    descriptors = [(0x10100, ENTRY), (0x10201, BASE)]
    if blocks is not None:
        descriptors.append((0x3FF, FMT_OFFSET))
    buf = bytearray(PAYLOAD + len(image))
    buf[0:4] = b"XEX2"
    buf[8:12] = struct.pack(">I", PAYLOAD)
    buf[16:20] = struct.pack(">I", SECURITY)
    buf[20:24] = struct.pack(">I", len(descriptors))
    at = 24
    for key, value in descriptors:
        buf[at:at + 8] = struct.pack(">II", key, value)
        at += 8
    size = sum(d + z for d, z in blocks) if blocks else len(image)
    buf[SECURITY:SECURITY + 4] = struct.pack(">I", 0x40)  # security_size
    buf[SECURITY + 4:SECURITY + 8] = struct.pack(">I", size)  # image_size
    if blocks is not None:
        struct.pack_into(">IHH", buf, FMT_OFFSET, 8 + 8 * len(blocks), 0, 1)  # unencrypted, basic compression
        for i, (data_size, zero_size) in enumerate(blocks):
            struct.pack_into(">II", buf, FMT_OFFSET + 8 + i * 8, data_size, zero_size)
    buf[PAYLOAD:PAYLOAD + len(image)] = image
    return bytes(buf)


def _unknown_rows() -> list[dict]:
    return [{"entry": 0x1000, "end": 0x1010, "name": "FUN", "convention": "unknown",
             "purge": 0, "params": 0, "thunk": False}]


def test_functions_require_conventions_by_default(monkeypatch, tmp_path: Path):
    """All-unknown conventions stop the PC import by default, but not when a fixed-ABI caller opts out."""
    rows = _unknown_rows()
    monkeypatch.setattr(ghidra, "available", lambda: True)
    monkeypatch.setattr(ghidra, "_run_script", lambda *args, **kwargs: rows)
    program = tmp_path / "game.exe"
    program.write_bytes(b"MZ" + bytes(64))

    with pytest.raises(ForkError, match="can't tell the functions' calling conventions"):
        ghidra.functions(program)
    assert ghidra.functions(program, require_conventions=False) == rows


def test_functions_no_decompiler_signal_stops_even_without_conventions(monkeypatch, tmp_path: Path):
    """Opting out of conventions must not swallow the explicit 'decompiler didn't run' signal."""
    rows = _unknown_rows()

    def _run(binary, script, timeout, *, analyse=True, said=None):
        if said is not None:
            said.append("ERROR os/mac_arm_64/decompile does not exist (DecompileProcessFactory)")
        return rows

    monkeypatch.setattr(ghidra, "available", lambda: True)
    monkeypatch.setattr(ghidra, "_run_script", _run)
    program = tmp_path / "game.exe"
    program.write_bytes(b"MZ" + bytes(64))

    with pytest.raises(ForkError, match="decompiler didn't run"):
        ghidra.functions(program, require_conventions=False)


def test_create_with_basic_compression_judges_the_expanded_image(tmp_path: Path):
    """A compressed XEX2's project points at the expanded image, with offsets measured from 0, not the payload."""
    data = bytes((i + 1) for i in range(0x20))
    blocks = [(0x10, 0x10), (0x10, 0x10)]
    expanded = data[:0x10] + bytes(0x10) + data[0x10:] + bytes(0x10)  # 0x40 bytes, as image_size says
    assert len(expanded) == 0x40

    game = tmp_path / "MyGame.xex"
    game.write_bytes(_xex2(data, blocks))
    (tmp_path / "functions.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in [
            {"a": f"{BASE:x}", "end": f"{BASE + 0x10:x}", "n": "entry"},
            {"a": f"{BASE + 0x20:x}", "end": f"{BASE + 0x30:x}", "n": "helper"},
        ]), encoding="utf-8")

    root = tmp_path / "project"
    info = XBOX360.create_project(root, game)
    cfg = json.loads((root / "config" / info["id"] / "functions.json").read_text(encoding="utf-8"))

    assert cfg["elf"] == "orig/MyGame.image.bin"
    assert cfg["sha1"] == hashlib.sha1(expanded).hexdigest()
    assert cfg["text"] == {"va": BASE, "offset": 0, "size": 0x40}
    assert (root / "orig" / "MyGame.image.bin").read_bytes() == expanded
    assert (root / "orig" / "MyGame.xex").read_bytes() == game.read_bytes()  # raw kept for provenance

    offsets = {}
    for fns in cfg["units"].values():
        for fn in fns:
            assert fn["offset"] == fn["addr"] - BASE
            offsets[fn["name"]] = fn["offset"]
    assert offsets == {"entry": 0, "helper": 0x20}


def test_ghidra_functions_uses_the_chosen_base_and_skips_conventions(tmp_path: Path, monkeypatch):
    """The Ghidra path passes require_conventions=False and converts VAs with the caller's base offset."""
    seen = {}

    def _fake_functions(program: Path, *, require_conventions: bool = True):
        seen["require"] = require_conventions
        return [{"entry": f"{BASE + 0x20:x}", "end": f"{BASE + 0x30:x}", "name": "f", "thunk": False}]

    monkeypatch.setattr(ghidra, "available", lambda: True)
    monkeypatch.setattr(ghidra, "functions", _fake_functions)
    game = tmp_path / "MyGame.xex"
    game.write_bytes(_xex2(bytes(range(0x40))))
    xex = scratch.read_xex2(game.read_bytes())

    got = scratch.ghidra_functions(game, xex, base_offset=0)
    assert seen["require"] is False
    assert got[0]["offset"] == 0x20
