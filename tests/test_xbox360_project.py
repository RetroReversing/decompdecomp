"""Stage 3 Xbox 360: function-map parsing and PROJECT scaffolding, with no Ghidra and no toolchain."""

import json
import struct
from pathlib import Path

import pytest

from decompnet.consoles.xbox360 import XBOX360, scratch
from decompnet.forks.project import ForkError

BASE = 0x80000000
ENTRY = 0x80000100
PAYLOAD = 0x1000
SECURITY = 0x200
IMAGE_SIZE = 0x4000


def _xex2(*, base=BASE, entry=ENTRY, payload=PAYLOAD, security=SECURITY, size=IMAGE_SIZE) -> bytes:
    """A tiny, self-consistent XEX2 header (plus its image span) for the parser to read."""
    descriptors = [(0x10100, entry), (0x10201, base)]
    buf = bytearray(payload + size)
    buf[0:4] = b"XEX2"
    buf[8:12] = struct.pack(">I", payload)
    buf[16:20] = struct.pack(">I", security)
    buf[20:24] = struct.pack(">I", len(descriptors))
    at = 24
    for key, value in descriptors:
        buf[at:at + 8] = struct.pack(">II", key, value)
        at += 8
    buf[security:security + 4] = struct.pack(">I", 0x40)  # security_size
    buf[security + 4:security + 8] = struct.pack(">I", size)  # image_size
    return bytes(buf)


def _row(a: int, end: int, name: str | None = None, ranges=None) -> dict:
    row = {"a": f"{a:x}", "end": f"{end:x}"}
    if name:
        row["n"] = name
    if ranges:
        row["ranges"] = [[f"{lo:x}", f"{hi:x}"] for lo, hi in ranges]
    return row


def _game(tmp_path: Path, rows: list[dict] | None, name: str = "MyGame.xex") -> Path:
    game = tmp_path / name
    game.write_bytes(_xex2())
    if rows is not None:
        (tmp_path / "functions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return game


def _context(game: Path):
    return scratch.game_info(game), scratch.read_xex2(game.read_bytes())


def test_map_functions_converts_va_to_file_offset(tmp_path: Path):
    game = _game(tmp_path, [
        _row(BASE, BASE + 0x10, "entry"),
        _row(BASE + 0x20, BASE + 0x30, "helper", ranges=[(BASE + 0x20, BASE + 0x28)]),
    ])
    info, xex = _context(game)
    got = scratch.map_functions(game, info, xex)
    assert got == [
        {"name": "entry", "addr": BASE, "offset": PAYLOAD, "size": 0x10, "ranges": []},
        {"name": "helper", "addr": BASE + 0x20, "offset": PAYLOAD + 0x20, "size": 0x10,
         "ranges": [[BASE + 0x20, BASE + 0x28]]},
    ]


def test_map_functions_skips_bad_lines_but_rejects_bad_extents(tmp_path: Path):
    game = _game(tmp_path, [{"not": "a function"}, _row(BASE + 0x40, BASE + 0x50, "ok")])
    info, xex = _context(game)
    got = scratch.map_functions(game, info, xex)
    assert len(got) == 1 and got[0]["name"] == "ok"

    (tmp_path / "bad").mkdir()
    bad = _game(tmp_path / "bad", [_row(BASE + IMAGE_SIZE, BASE + IMAGE_SIZE + 0x10, "oob")])
    info, xex = _context(bad)
    with pytest.raises(ForkError, match="outside the image"):
        scratch.map_functions(bad, info, xex)


def test_map_functions_rejects_ambiguous_maps(tmp_path: Path):
    game = _game(tmp_path, None)
    for name in ("a.functions.jsonl", "b.functions.jsonl"):
        (tmp_path / name).write_text(json.dumps(_row(BASE, BASE + 0x10)) + "\n", encoding="utf-8")
    info, xex = _context(game)
    with pytest.raises(ForkError, match="more than one function map"):
        scratch.map_functions(game, info, xex)


def test_create_builds_a_judgeable_project(tmp_path: Path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, root, what, log):
        calls.append(cmd)
        if cmd[-1] == "prepare-targets":
            # Stand in for delink.py (no real ninja or xsdk kit): write one target object per unit so create()'s
            # post-build check passes.
            cfg = json.loads((root / "config" / "MyGame" / "functions.json").read_text(encoding="utf-8"))
            target_dir = root / "build" / "MyGame" / "target"
            target_dir.mkdir(parents=True, exist_ok=True)
            for unit in cfg["units"]:
                (target_dir / f"{unit}.o").write_bytes(b"")
        return (True, "")

    monkeypatch.setattr(scratch, "_run", fake_run)
    game = _game(tmp_path, [
        _row(BASE, BASE + 0x10, "entry"),
        _row(BASE + 0x2000, BASE + 0x2010, "far_away"),  # the gap makes a second unit
    ])
    root = tmp_path / "project"
    info = XBOX360.create_project(root, game)

    # The first build configures the project, then delinks target objects only (candidates need the user's kit).
    assert any("tools/configure.py" in cmd and "MyGame" in cmd for cmd in calls)
    assert [cmd[-1] for cmd in calls][-1] == "prepare-targets"
    assert info["platform"] == "xbox360" and info["sources"] == 2
    assert info["id"] == "MyGame" and info["sha1"] == scratch.game_info(game)["sha1"]

    cfg = json.loads((root / "config" / info["id"] / "functions.json").read_text(encoding="utf-8"))
    assert cfg["target_format_version"] == 4
    assert cfg["elf"] == "orig/MyGame.xex"
    assert cfg["image_base"] == BASE and cfg["entry"] == ENTRY
    assert cfg["text"] == {"va": BASE, "offset": PAYLOAD, "size": IMAGE_SIZE}
    assert len(cfg["units"]) == 2
    names = {fn["name"] for fns in cfg["units"].values() for fn in fns}
    assert names == {"entry", "far_away"}
    for fns in cfg["units"].values():
        for fn in fns:
            assert set(fn) == {"name", "addr", "offset", "size", "relocs"}
            assert fn["relocs"] == []
            assert fn["offset"] == fn["addr"] - BASE + PAYLOAD

    assert (root / "orig" / "MyGame.xex").read_bytes() == game.read_bytes()
    assert (root / "include" / "common.h").is_file()
    assert len(list((root / "src" / "auto").glob("*.c"))) == 2
    for src in (root / "src" / "auto").glob("*.c"):
        assert '#include "common.h"' in src.read_text(encoding="utf-8")
    objdiff = json.loads((root / "objdiff.json").read_text(encoding="utf-8"))
    assert len(objdiff["units"]) == 2
    assert (root / "tools" / "configure.py").is_file() and (root / "tools" / "compile.py").is_file()
    manifest = json.loads((root / ".decomp.wtf.json").read_text(encoding="utf-8"))
    assert manifest["platform"] == "xbox360" and manifest["version"] == info["id"]
    assert manifest["setup"] == [["python", "tools/configure.py", "MyGame"], ["ninja", "prepare"]]


def test_create_reports_missing_target_objects(tmp_path: Path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, root, what, log):
        calls.append(cmd)
        return (True, "")  # no target objects written

    monkeypatch.setattr(scratch, "_run", fake_run)
    game = _game(tmp_path, [_row(BASE, BASE + 0x10, "entry")])
    with pytest.raises(ForkError, match="target object"):
        XBOX360.create_project(tmp_path / "project", game)
    assert [cmd[-1] for cmd in calls][-1] == "prepare-targets"


def test_create_without_a_map_has_an_actionable_error(tmp_path: Path, monkeypatch):
    from decompnet.forks import ghidra

    # This test claims to check the no-Ghidra, no-map error. Ghidra is installed here, so pin it as unavailable
    # rather than depending on whichever error the (now working) Ghidra path happens to raise.
    monkeypatch.setattr(ghidra, "available", lambda: False)
    game = _game(tmp_path, None)
    with pytest.raises(ForkError, match="functions.jsonl"):
        XBOX360.create_project(tmp_path / "project", game)
    assert not (tmp_path / "project").exists()
