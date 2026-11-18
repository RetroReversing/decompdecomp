"""Synthetic XEX2 tests for basic-compressed image bodies.

A real retail XEX2 can have an image body that is compressed, so the bytes after the header are not the memory
image. These tests build tiny, self-consistent XEX2 files with a 0x3FF (file-format) descriptor and a block table,
then check the expansion, the Ghidra wrapper's PT_LOAD and the errors for bodies decompdecomp cannot handle. No
Ghidra, toolchain or real game is involved.
"""

import struct
from pathlib import Path

import pytest

from decompnet.consoles.xbox360 import scratch
from decompnet.forks.project import ForkError

BASE = 0x80000000
ENTRY = 0x80000100
SECURITY = 0x200
SECURITY_SIZE = 0x40
FF_OFFSET = 0x100  # where the 0x3FF file-format descriptor lives, before the security header
PAYLOAD = 0x400
BLOCKS = [(6, 4), (3, 2)]  # two blocks: 9 data bytes on disk, 6 zero bytes in memory
BODY = b"abcdefghi"
EXPECTED = b"abcdef" + bytes(4) + b"ghi" + bytes(2)
IMAGE_SIZE = len(EXPECTED)

EHDR = ">16sHHIQQQIHHHHHH"  # 64-byte ELF64 header
PHDR = ">IIQQQQQQ"          # 56-byte ELF64 program header


def _xex2(body: bytes, image_size: int, *, blocks: list[tuple[int, int]] | None = None,
          compression: int = 0, encryption: int = 0, entry: int | None = ENTRY) -> bytes:
    """Build a small XEX2 with an optional 0x3FF basic-compression descriptor and body bytes at PAYLOAD."""
    descriptors = [(0x10201, BASE)]
    if entry is not None:
        descriptors.append((0x10100, entry))
    if blocks is not None:
        descriptors.append((0x3FF, FF_OFFSET))
    buf = bytearray(PAYLOAD + len(body))
    buf[0:4] = b"XEX2"
    struct.pack_into(">I", buf, 8, PAYLOAD)
    struct.pack_into(">I", buf, 16, SECURITY)
    struct.pack_into(">I", buf, 20, len(descriptors))
    at = 24
    for key, value in descriptors:
        struct.pack_into(">II", buf, at, key, value)
        at += 8
    struct.pack_into(">II", buf, SECURITY, SECURITY_SIZE, image_size)
    if blocks is not None:
        struct.pack_into(">IHH", buf, FF_OFFSET, 8 + 8 * len(blocks), encryption, compression)
        at = FF_OFFSET + 8
        for data_size, zero_size in blocks:
            struct.pack_into(">II", buf, at, data_size, zero_size)
            at += 8
    buf[PAYLOAD:PAYLOAD + len(body)] = body
    return bytes(buf)


def _game(tmp_path: Path, data: bytes, name: str = "game.xex") -> tuple[Path, dict]:
    game = tmp_path / name
    game.write_bytes(data)
    return game, scratch.read_xex2(data)


def _header(data: bytes) -> dict:
    names = ("ident", "type", "machine", "version", "entry", "phoff", "shoff", "flags",
             "ehsize", "phentsize", "phnum", "shentsize", "shnum", "shstrndx")
    return dict(zip(names, struct.unpack_from(EHDR, data, 0)))


def test_read_xex2_carries_the_raw_header_directory():
    data = _xex2(BODY, IMAGE_SIZE, blocks=BLOCKS, compression=1)
    xex = scratch.read_xex2(data)
    assert xex["payload_offset"] == PAYLOAD and xex["image_size"] == IMAGE_SIZE
    assert xex["image_base"] == BASE and xex["entry_point"] == ENTRY
    assert xex["headers"][0x3FF] == FF_OFFSET
    assert xex["headers"][0x10201] == BASE


def test_read_file_format_parses_a_basic_block_table():
    data = _xex2(BODY, IMAGE_SIZE, blocks=BLOCKS, compression=1)
    xex = scratch.read_xex2(data)
    assert scratch.read_file_format(data, xex) == {"encryption": 0, "compression": 1, "blocks": BLOCKS}

    # no 0x3FF descriptor (and no headers field at all) means a plain, unencrypted image
    plain = _xex2(b"abcd", 4)
    assert scratch.read_file_format(plain, scratch.read_xex2(plain)) == {
        "encryption": 0, "compression": 0, "blocks": []}
    assert scratch.read_file_format(plain, {"payload_offset": PAYLOAD, "image_size": 4}) == {
        "encryption": 0, "compression": 0, "blocks": []}


def test_expand_basic_image_reconstructs_the_memory_image():
    data = _xex2(BODY, IMAGE_SIZE, blocks=BLOCKS, compression=1)
    xex = scratch.read_xex2(data)
    fmt = scratch.read_file_format(data, xex)
    assert scratch.expand_basic_image(data, xex, fmt) == EXPECTED
    assert len(scratch.expand_basic_image(data, xex, fmt)) == xex["image_size"]


def test_ghidra_image_maps_the_expanded_image(tmp_path: Path):
    game, xex = _game(tmp_path, _xex2(BODY, IMAGE_SIZE, blocks=BLOCKS, compression=1))
    wrapper = scratch.ghidra_image(game, xex, tmp_path)
    data = wrapper.read_bytes()
    h = _header(data)
    assert h["ident"][:6] == b"\x7fELF\x02\x02" and h["machine"] == 21
    assert h["entry"] == ENTRY and h["phoff"] == 64 and h["phentsize"] == 56 and h["phnum"] == 1
    p_type, p_flags, p_offset, p_vaddr, p_paddr, p_filesz, p_memsz, p_align = struct.unpack_from(PHDR, data, h["phoff"])
    assert p_type == 1 and p_flags & 0x1
    assert p_vaddr == p_paddr == BASE
    assert p_offset == 120 and p_filesz == p_memsz == IMAGE_SIZE
    assert data[p_offset:p_offset + p_filesz] == EXPECTED
    assert len(data) == p_offset + IMAGE_SIZE


def test_expand_basic_image_rejects_a_truncated_body(tmp_path: Path):
    # the first block fits, but the second one needs 3 bytes and only 1 is on disk
    game, xex = _game(tmp_path, _xex2(b"abcdefg", IMAGE_SIZE, blocks=BLOCKS, compression=1))
    data = game.read_bytes()
    fmt = scratch.read_file_format(data, xex)
    with pytest.raises(ForkError, match="truncated"):
        scratch.expand_basic_image(data, xex, fmt)


def test_expand_basic_image_rejects_a_block_table_that_does_not_fill_the_image():
    blocks = [(6, 2)]  # 8 bytes of image, but the header declares IMAGE_SIZE
    data = _xex2(b"abcdef", IMAGE_SIZE, blocks=blocks, compression=1)
    xex = scratch.read_xex2(data)
    with pytest.raises(ForkError, match="expands to"):
        scratch.expand_basic_image(data, xex, scratch.read_file_format(data, xex))


def test_expand_basic_image_without_compression():
    image = bytes((i * 7 + 3) & 0xFF for i in range(16))
    data = _xex2(image, len(image))
    xex = scratch.read_xex2(data)
    fmt = scratch.read_file_format(data, xex)
    assert scratch.expand_basic_image(data, xex, fmt) == image

    # hand-built xex dicts without a headers field stay compatible
    assert scratch.expand_basic_image(data, {"payload_offset": PAYLOAD, "image_size": len(image)}, fmt) == image

    short = _xex2(image[:4], len(image))
    short_xex = scratch.read_xex2(short)
    with pytest.raises(ForkError, match="truncated"):
        scratch.expand_basic_image(short, short_xex, scratch.read_file_format(short, short_xex))


@pytest.mark.parametrize(("encryption", "compression", "message"), [
    (1, 1, "encrypted"),
    (0, 2, "compression type 2"),
    (0, 3, "compression type 3"),
])
def test_expand_basic_image_refuses_bodies_it_cannot_expand(encryption: int, compression: int, message: str):
    data = _xex2(BODY, IMAGE_SIZE, blocks=BLOCKS, compression=compression, encryption=encryption)
    xex = scratch.read_xex2(data)
    fmt = scratch.read_file_format(data, xex)
    with pytest.raises(ForkError, match=message):
        scratch.expand_basic_image(data, xex, fmt)


def test_ghidra_image_refuses_an_encrypted_body(tmp_path: Path):
    game, xex = _game(tmp_path, _xex2(BODY, IMAGE_SIZE, blocks=BLOCKS, compression=1, encryption=1))
    with pytest.raises(ForkError, match="encrypted"):
        scratch.ghidra_image(game, xex, tmp_path)
    assert not (tmp_path / "xex2-ppc64.elf").exists()
