"""Stage 1 Xbox 360 support: sniff a synthetic XEX2, read its header and report game info."""

import hashlib
import struct
from pathlib import Path

import pytest

from decompnet.consoles import get
from decompnet.consoles.xbox360 import XBOX360
from decompnet.consoles.xbox360.scratch import game_info, is_xex2, read_xex2


def _xex2(*, base=0x80000000, entry=0x80000100, payload=0x1000, security=0x200, size=0x400):
    """A tiny, self-consistent XEX2 header (no game data) for the parser to read."""
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


def test_sniff_accepts_a_plausible_xex2():
    data = _xex2()
    assert is_xex2(data)
    assert XBOX360.sniff(data, data)
    assert get("xbox360") is XBOX360


def test_sniff_rejects_other_files():
    assert not is_xex2(b"")
    assert not is_xex2(b"XEX2")  # too short to hold the offsets
    zeros = bytes(0x100)
    assert not XBOX360.sniff(zeros, zeros)
    assert not XBOX360.sniff(b"NOPE" + zeros, b"NOPE" + zeros)


def test_read_xex2_returns_header_fields():
    data = _xex2(base=0x80000000, entry=0x80000100)
    got = read_xex2(data)
    assert got["payload_offset"] == 0x1000
    assert got["security_offset"] == 0x200
    assert got["security_size"] == 0x40
    assert got["image_size"] == 0x400
    assert got["image_base"] == 0x80000000
    assert got["entry_point"] == 0x80000100


def test_read_xex2_rejects_non_xex2():
    with pytest.raises(ValueError):
        read_xex2(bytes(0x100))


def test_game_info(tmp_path: Path):
    data = _xex2()
    path = tmp_path / "MyGame.xex"
    path.write_bytes(data)
    info = game_info(path)
    assert info["platform"] == "xbox360"
    assert info["id"] == "MyGame" and info["title"] == "MyGame"
    assert info["sha1"] == hashlib.sha1(data).hexdigest()
    assert info["image_base"] == 0x80000000


def test_upload_name(tmp_path: Path):
    assert XBOX360.upload_name(b"anything") == "game.xex"
