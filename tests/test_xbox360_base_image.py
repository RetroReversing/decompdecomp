"""A decrypted Xbox 360 base image (a PowerPC PE as `xextool -b` writes one) is a game file this console imports.

The image is in FILE layout — sections at their raw offsets, smaller than the image they map to — while the rest of
the console works in one flat image where a VA's offset is `va - image_base`. These build a tiny PE and check the
mapping, the facts, and that the page/app recognise it (no Ghidra, no real game).
"""

import struct

import pytest

from decompnet import consoles
from decompnet.consoles.xbox360 import XBOX360, scratch
from decompnet.forks.project import ForkError

IMAGE_BASE = 0x82000000
PAYLOAD = b"\x7c\x08\x02\xa6" * 16  # 64 bytes of plausible PowerPC


def build_pe(machine: int = scratch.PE_MACHINE_PPC, *, virtual_address: int = 0x2000,
             image_size: int = 0x3000, sections: int = 1, raw: bytes = PAYLOAD) -> bytes:
    """A minimal PE32: DOS header, COFF header, optional header, one section header and its raw bytes."""
    data = bytearray(0x400 + len(raw))
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x40)
    lfanew = 0x40
    data[lfanew:lfanew + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, lfanew + 4, machine, sections, 0, 0, 0, 0xE0, 0x102)
    opt = lfanew + 24
    struct.pack_into("<H", data, opt, 0x10B)
    struct.pack_into("<I", data, opt + 16, virtual_address)  # AddressOfEntryPoint (an RVA)
    struct.pack_into("<I", data, opt + 28, IMAGE_BASE)
    struct.pack_into("<I", data, opt + 56, image_size)  # SizeOfImage
    struct.pack_into("<I", data, opt + 92, 16)  # NumberOfRvaAndSizes
    section = opt + 0xE0
    struct.pack_into("<8sIIII", data, section, b".text\0\0\0", len(raw), virtual_address, len(raw), 0x400)
    data[0x400:0x400 + len(raw)] = raw
    return bytes(data)


def test_a_powerpc_pe_is_a_base_image_and_an_x86_one_is_not():
    assert scratch.is_base_image(build_pe()) is True
    assert scratch.is_base_image(build_pe(machine=0x14C)) is False  # a Windows x86 program
    assert scratch.is_base_image(b"XEX2" + bytes(64)) is False
    assert scratch.is_base_image(b"") is False


def test_the_image_is_mapped_to_where_its_sections_belong():
    """The file is not the image: the section's bytes must appear at its virtual address in the flat image."""
    mapped = scratch.read_base_image(build_pe())

    assert mapped["image_base"] == IMAGE_BASE
    assert mapped["image_size"] == 0x3000
    assert mapped["entry_point"] == IMAGE_BASE + 0x2000
    assert len(mapped["image"]) == 0x3000
    assert mapped["image"][0x2000:0x2000 + len(PAYLOAD)] == PAYLOAD
    assert mapped["image"][:0x100] == bytes(0x100)  # nothing invented before the first section


def test_a_file_this_cannot_be_is_refused_with_a_reason():
    with pytest.raises(ForkError, match="not a PowerPC"):
        scratch.read_base_image(build_pe(machine=0x14C))
    with pytest.raises(ForkError, match="no header to read|truncated"):
        scratch.read_base_image(b"")
    # a section that ends past the image it claims
    with pytest.raises(ForkError, match="past its own image size"):
        scratch.read_base_image(build_pe(virtual_address=0x2FFF, image_size=0x3000))


def test_a_basefile_that_ends_before_a_section_is_padded_not_shrunk():
    """A dump can stop before its last section does. Taking a short slice of the buffer would RESIZE it (Python
    shrinks a bytearray when a shorter sequence is assigned), so the image must be padded to its stated size."""
    data = bytearray(build_pe(image_size=0x4000))
    lfanew, header_size = 0x40, 0xE0
    section = lfanew + 24 + header_size
    struct.pack_into("<I", data, section + 8, len(PAYLOAD) + 0x1000)   # VirtualSize
    struct.pack_into("<I", data, section + 16, len(PAYLOAD) + 0x1000)  # SizeOfRawData (more than the file holds)

    mapped = scratch.read_base_image(bytes(data))

    assert mapped["image_size"] == 0x4000
    assert len(mapped["image"]) == 0x4000  # not shrunk to the bytes present
    assert mapped["image"][0x2000:0x2000 + len(PAYLOAD)] == PAYLOAD


def test_the_xextool_kit_exposes_the_tool_and_conversion_needs_it(tmp_path, monkeypatch):
    """XexTool is the user's own copy: a kit names its folder, and without one the import falls back to the reason."""
    from decompnet import kits
    from decompnet.kits.xextool import Xextool

    folder = tmp_path / "xextool_v6.6"
    folder.mkdir()
    (folder / "xextool.exe").write_bytes(b"MZ" + bytes(64))

    kit = kits.get("xextool")
    assert kit is not None and kit.id == "xextool"
    assert kit.locate(folder) == folder.resolve()
    tool = Xextool().compilers(folder)[0]
    assert tool.version == "xextool" and tool.runner == "wine"

    monkeypatch.setattr(kits, "compilers", lambda ids=None: [])
    assert scratch.xextool_base_image(tmp_path / "game.xex", log=lambda line: None) is None


def test_the_app_recognises_a_base_image_as_an_xbox_360_game(tmp_path):
    path = tmp_path / "default.exe"
    path.write_bytes(build_pe())

    assert consoles.identify(path).id == "xbox360"
    assert XBOX360.upload_name(build_pe()) == "default.exe"
    info = scratch.game_info(path)
    assert info["image_base"] == IMAGE_BASE and info["entry_point"] == IMAGE_BASE + 0x2000
