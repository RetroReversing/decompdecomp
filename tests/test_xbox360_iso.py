"""Xbox 360 disc images: the XDVDFS reader, on a tiny image built here (no real disc needed)."""

import struct
from pathlib import Path

import pytest

from decompnet.consoles.xbox360 import iso

XEX = b"XEX2" + bytes(24) + b"pretend game"


def _table(entries: list[tuple[str, bool, int, int]]) -> bytes:
    """A directory table: each entry right-links to the next, which is how XDVDFS stores a directory."""
    out = bytearray()
    starts = []
    for name, is_directory, sector, size in entries:
        starts.append(len(out))
        out += struct.pack("<HHIIBB", 0, 0, sector, size, iso.ATTR_DIRECTORY if is_directory else 0, len(name))
        out += name.encode("latin-1")
        out += b"\0" * (-len(out) % 4)
    for index in range(len(entries) - 1):
        struct.pack_into("<H", out, starts[index] + 2, (starts[index + 1] - starts[index]) // 4)
    return bytes(out)


def make_disc(path: Path, *, partition: int = 0) -> Path:
    """A disc image with default.xex, a Content/data.bin and a directory, at ``partition``'s offset."""
    sectors = 64
    image = bytearray(partition + sectors * iso.SECTOR)
    base = partition
    root = _table([("Content", True, 34, 32), ("default.xex", False, 35, len(XEX))])
    content = _table([("data.bin", False, 36, 4)])
    image[base + 33 * iso.SECTOR:base + 33 * iso.SECTOR + len(root)] = root
    image[base + 34 * iso.SECTOR:base + 34 * iso.SECTOR + len(content)] = content
    image[base + 35 * iso.SECTOR:base + 35 * iso.SECTOR + len(XEX)] = XEX
    image[base + 36 * iso.SECTOR:base + 36 * iso.SECTOR + 4] = b"data"
    descriptor = base + iso.DESCRIPTOR
    image[descriptor:descriptor + 6] = iso.MAGIC
    struct.pack_into("<II", image, descriptor + 0x08, 33, len(root))
    path.write_bytes(bytes(image))
    return path


def test_entries_and_files_come_out_of_the_volume(tmp_path: Path):
    """The reader lists the volume's tree and returns a file's bytes."""
    with iso.Disc(make_disc(tmp_path / "game.iso")) as disc:
        paths = {entry.path for entry in disc.entries()}
        assert {"Content", "Content/data.bin", "default.xex"} <= paths
        assert disc.read("default.xex") == XEX
        assert disc.read("Content/data.bin") == b"data"


def test_the_boot_executable_is_the_discs_default_xex(tmp_path: Path):
    """Importing needs the game's own executable, which is the XEX2 at the root."""
    with iso.Disc(make_disc(tmp_path / "game.iso")) as disc:
        assert disc.boot_executable() == XEX


def test_a_disc_with_a_video_partition_is_found_at_its_offset(tmp_path: Path):
    """XGD2/XGD3 images keep the video partition first; the game's volume starts further in."""
    path = make_disc(tmp_path / "xgd2.iso", partition=0xFD90000)
    with iso.Disc(path) as disc:
        assert disc.base == 0xFD90000
        assert disc.read("default.xex") == XEX


def test_what_it_cannot_read_says_so(tmp_path: Path):
    """A file that isn't a disc, a missing file and a directory read are IsoError, not a crash."""
    plain = tmp_path / "not-a-disc.bin"
    plain.write_bytes(b"nothing like a disc image")
    with pytest.raises(iso.IsoError, match="no XDVDFS volume"):
        iso.Disc(plain)

    with iso.Disc(make_disc(tmp_path / "game.iso")) as disc:
        with pytest.raises(iso.IsoError, match="isn't on this disc"):
            disc.read("nope.bin")
        with pytest.raises(iso.IsoError, match="is a directory"):
            disc.read("Content")


def test_extracting_default_xex_writes_it(tmp_path: Path):
    """The one call importing makes: take the game's executable out and leave it somewhere."""
    written = iso.extract_default_xex(make_disc(tmp_path / "game.iso"), tmp_path / "default.xex")
    assert written.read_bytes() == XEX
