"""Xbox 360 disc images: the XDVDFS reader, on a tiny image built here (no real disc needed)."""

import struct
from pathlib import Path

import pytest

from decompnet.consoles.xbox360 import iso

XEX = b"XEX2" + bytes(24) + b"pretend game"


def _table(entries: list[tuple[str, bool, int, int]]) -> bytes:
    """A directory table: each entry right-links to the next, in 4-byte units from the start of the table."""
    out = bytearray()
    starts = []
    for name, is_directory, sector, size in entries:
        starts.append(len(out))
        out += struct.pack("<HHIIBB", 0, 0, sector, size, iso.ATTR_DIRECTORY if is_directory else 0, len(name))
        out += name.encode("latin-1")
        out += b"\0" * (-len(out) % 4)
    for index in range(len(entries) - 1):
        struct.pack_into("<H", out, starts[index] + 2, starts[index + 1] // 4)  # absolute, in 4-byte units
    return bytes(out)


def make_disc(path: Path, *, partition: int = 0, boot: str = "default.xex", layout: str = "disc") -> Path:
    """A disc image with the boot executable, a Content/data.bin and a directory, at ``partition``'s offset.

    ``layout`` is "disc" for a shipping disc ("MICROSOFT*XBOX*MEDIA", root at 0x14) or "tool" for an image made by
    tooling ("XDVDFS", root at 0x08): both exist in the wild and the reader must read either.
    """
    sectors = 64
    image = bytearray(partition + sectors * iso.SECTOR)
    base = partition
    root = _table([("Content", True, 34, 32), (boot, False, 35, len(XEX))])
    content = _table([("data.bin", False, 36, 4)])
    image[base + 33 * iso.SECTOR:base + 33 * iso.SECTOR + len(root)] = root
    image[base + 34 * iso.SECTOR:base + 34 * iso.SECTOR + len(content)] = content
    image[base + 35 * iso.SECTOR:base + 35 * iso.SECTOR + len(XEX)] = XEX
    image[base + 36 * iso.SECTOR:base + 36 * iso.SECTOR + 4] = b"data"
    descriptor = base + iso.DESCRIPTOR
    if layout == "tool":
        image[descriptor:descriptor + 6] = iso.MAGIC_TOOL
        struct.pack_into("<II", image, descriptor + 0x08, 33, len(root))
    else:
        image[descriptor:descriptor + 20] = iso.MAGIC
        struct.pack_into("<II", image, descriptor + 0x14, 33, len(root))
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


def test_a_tool_made_image_with_the_other_magic_reads_too(tmp_path: Path):
    """Images repacked by tooling carry "XDVDFS" and put the root at 0x08; both layouts exist in the wild."""
    with iso.Disc(make_disc(tmp_path / "repack.iso", layout="tool")) as disc:
        assert disc.read("default.xex") == XEX


def test_a_disc_spelling_it_Default_xex_is_still_found(tmp_path: Path):
    """Real discs differ on case ('Default.xex'), and the sniff looks for the lower-case name."""
    with iso.Disc(make_disc(tmp_path / "caps.iso", boot="Default.xex")) as disc:
        assert disc.find("default.xex").path == "Default.xex"
        assert disc.boot_executable() == XEX


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


def test_a_disc_is_identified_as_xbox_360_not_a_playstation(tmp_path: Path):
    """A full disc dump starts with a DVD-video partition whose ISO 9660 filesystem a PlayStation sniff claimed;
    its XDVDFS volume with a default.xex is what makes it an Xbox 360 disc."""
    from decompnet import consoles
    from decompnet.consoles.xbox360 import XBOX360

    path = make_disc(tmp_path / "game.iso")
    with path.open("r+b") as handle:  # the video partition's volume descriptor
        handle.seek(16 * iso.SECTOR)
        handle.write(b"\x01CD001\x01")

    assert consoles.identify(path).id == "xbox360"
    assert XBOX360.upload_name(path.read_bytes()) == "game.iso"
    assert XBOX360.upload_name(XEX) == "game.xex"


def test_an_original_xbox_disc_is_not_claimed_by_the_xbox_360(tmp_path: Path):
    """Its boot executable is default.xbe, so the Xbox 360 sniff leaves it to the Xbox even though the filesystem
    is the same XDVDFS."""
    from decompnet.consoles.xbox360 import XBOX360

    path = make_disc(tmp_path / "xbox.iso", boot="default.xbe")

    assert XBOX360.sniff(path.read_bytes()[:1024], path) is False


def test_a_disc_is_read_through_to_its_executable(tmp_path: Path):
    """Creating a project from a disc reads its default.xex, and remembers the disc it came from."""
    from decompnet.consoles.xbox360 import scratch

    extracted, disc = scratch._game_file(make_disc(tmp_path / "game.iso"), lambda line: None)

    assert extracted.name == "default.xex" and extracted.read_bytes() == XEX
    assert disc is not None and disc.name == "game.iso"


def test_a_file_that_is_neither_says_so(tmp_path: Path):
    """A file that is neither a XEX2 nor a disc is a ForkError naming both, not a stack trace."""
    from decompnet.consoles.xbox360 import scratch
    from decompnet.forks.project import ForkError

    plain = tmp_path / "nonsense.bin"
    plain.write_bytes(b"neither executable nor disc")

    with pytest.raises(ForkError, match="not a readable XEX2 program or an Xbox 360 disc image"):
        scratch._game_file(plain, lambda line: None)
