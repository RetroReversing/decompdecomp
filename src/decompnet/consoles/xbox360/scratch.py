"""Minimal XEX2 reader for the Xbox 360 console connector (stage 1).

Only the handful of header fields ``sniff()`` and ``game_info()`` need are read:
the magic, the payload/security offsets, the optional-header directory and the
image base. Nothing here validates a signature or loads the executable.

Format sources (documentation only, no code copied): the XEX2 header layout as
described in Xenia's ``src/xenia/kernel/util/xex2_info.h`` and as summarised by
Moonlight360's ``scripts/inspect-xex-contracts.py`` (GPL-3.0-or-later original
work). This module is an independent implementation from the documented offsets.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

MAGIC = b"XEX2"
MAX_DESCRIPTORS = 4096

# optional-header directory keys (big-endian u32 key -> u32 value)
K_ENTRY = 0x10100
K_IMAGE_BASE = 0x10201
K_IMPORT_TABLE = 0x103FF


def is_xex2(head: bytes) -> bool:
    """Whether ``head`` (a file's first bytes) looks like a structurally plausible XEX2."""
    if len(head) < 24 or head[:4] != MAGIC:
        return False
    # payload@8, security@16, count@20 (offset 12 is a different header field)
    payload = int.from_bytes(head[8:12], "big")
    security = int.from_bytes(head[16:20], "big")
    count = int.from_bytes(head[20:24], "big")
    table_end = 24 + count * 8
    # the header directory must fit before the security header, which (size +
    # image size) must in turn fit before the payload. Deliberately no check
    # against len(head): a picked file larger than the sniff window is fine.
    return count <= MAX_DESCRIPTORS and table_end <= security and security + 8 <= payload


def read_xex2(data: bytes) -> dict:
    """Read the few header fields needed by sniff/game_info from an XEX2 image.

    Raises ``ValueError`` for a short or implausible image; the caller decides
    whether that means "not this console" or "report what we can".
    """
    if not is_xex2(data):
        raise ValueError("not an XEX2 image")
    payload = int.from_bytes(data[8:12], "big")
    security = int.from_bytes(data[16:20], "big")
    count = int.from_bytes(data[20:24], "big")
    table_end = 24 + count * 8
    if len(data) < payload or table_end > len(data) or security + 8 > len(data):
        raise ValueError("truncated XEX2 header")
    security_size, image_size = struct.unpack(">II", data[security:security + 8])
    if security_size < 8 or security + security_size > payload:
        raise ValueError("implausible XEX2 security span")

    selected = {}
    for index in range(count):
        at = 24 + index * 8
        key, value = struct.unpack(">II", data[at:at + 8])
        if key not in selected:
            selected[key] = value
    return {
        "payload_offset": payload,
        "security_offset": security,
        "security_size": security_size,
        "image_size": image_size,
        "image_base": selected.get(K_IMAGE_BASE),
        "entry_point": selected.get(K_ENTRY),
        "import_table_offset": selected.get(K_IMPORT_TABLE),
    }


def game_info(game: Path) -> dict:
    """What an XEX2 file is: platform, an id/title from its name, its sha1, header facts."""
    data = game.read_bytes()
    info = {
        "platform": "xbox360",
        "id": game.stem,
        "title": game.stem,
        "sha1": hashlib.sha1(data).hexdigest(),
    }
    try:
        xex = read_xex2(data)
    except ValueError:
        return info
    if xex["image_base"] is not None:
        info["image_base"] = xex["image_base"]
    if xex["entry_point"] is not None:
        info["entry_point"] = xex["entry_point"]
    return info
