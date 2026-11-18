"""Xbox 360 disc images: the XDVDFS filesystem and the game's default.xex inside it.

An Xbox 360 disc is an ISO whose game data is an XDVDFS (Xbox DVD File System) volume: a volume descriptor at
sector 32 of the game partition naming the root directory, and directories as binary trees of 13-byte entries.
XGD2 and XGD3 discs put a video partition first, so the game partition begins at a fixed offset and the volume
descriptor is at that offset + 32 sectors; a "game only" rip (and every Xbox 1 disc) starts at zero.

Read-only by design: this lists a disc the user owns and takes the files they ask for out of it, which is what
importing needs -- decompdecomp never decrypts or modifies a disc image.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

SECTOR = 2048
# The volume descriptor's magic. A disc made by Xbox 360 tooling starts with "XDVDFS"; the shipping discs this app
# actually meets start with "MICROSOFT*XBOX*MEDIA", and the two put the root directory at different offsets.
MAGIC = b"MICROSOFT*XBOX*MEDIA"
MAGIC_TOOL = b"XDVDFS"
DESCRIPTOR = 32 * SECTOR  # the volume descriptor sits at sector 32 of the game partition
# Where the game partition starts on each disc generation: a game-only/base image, XGD2, XGD3.
PARTITIONS = (0, 0xFD90000, 0x2080000)
ATTR_DIRECTORY = 0x10
MAX_DEPTH = 32


class IsoError(Exception):
    """A disc image this reader can't place: no XDVDFS volume, a broken directory, or a missing file."""


def _descriptor(handle, offset: int) -> tuple[int, int] | None:
    """(root directory sector, its size) when a volume descriptor sits at ``offset``, else None.

    The magic decides where they are: "MICROSOFT*XBOX*MEDIA" keeps them at 0x14/0x18, tool-made images with the
    "XDVDFS" magic put them at 0x08/0x0C.
    """
    head = _read_at(handle, offset, 0x20)
    if head[:20] == MAGIC:
        return struct.unpack_from("<II", head, 0x14)
    if head[:len(MAGIC_TOOL)] == MAGIC_TOOL:
        return struct.unpack_from("<II", head, 0x08)
    return None


@dataclass(frozen=True)
class Entry:
    """One file or directory on the disc, by its full path ("default.xex", "Content/00000001/x")."""

    path: str
    sector: int
    size: int
    directory: bool


def _read_at(handle, offset: int, length: int) -> bytes:
    handle.seek(offset)
    return handle.read(length)


def game_partition(handle) -> int:
    """Where the game partition starts: the first offset whose volume descriptor is a sane XDVDFS one.

    A disc that also carries a video partition keeps the game's filesystem at a fixed offset per generation, and a
    rip of only the game data has it at zero, so the descriptor identifies the layout without the caller having to
    know which kind of disc it holds.
    """
    for base in PARTITIONS:
        found = _descriptor(handle, base + DESCRIPTOR)
        if found is None:
            continue
        root_sector, root_size = found
        if 0 < root_size <= 32 * 1024 * 1024 and root_sector * SECTOR < _size(handle):
            return base
    raise IsoError("no XDVDFS volume found: this isn't an Xbox 360 disc image (or it's a format this reader "
                   "doesn't know)")


def _size(handle) -> int:
    return handle.seek(0, 2) or handle.tell()


def _entries(handle, base: int, sector: int, size: int) -> list[Entry]:
    """Every entry of one directory table, walked as the binary tree it is (left/right child offsets).

    Children are walked with a work list rather than recursion: a directory with many files is a long chain of
    right-siblings, which is not nesting, and counting it as depth rejected real discs with more than ~32 entries.
    """
    table = _read_at(handle, base + sector * SECTOR, size)
    found: list[Entry] = []
    seen: set[int] = set()
    pending = [0]
    while pending:
        offset = pending.pop()
        if offset in seen or not 0 <= offset < len(table):
            continue
        seen.add(offset)
        left, right, start, length, attributes, name_length = struct.unpack_from("<HHIIBB", table, offset)
        name = table[offset + 14:offset + 14 + name_length].decode("latin-1")
        if name:
            found.append(Entry(name.rstrip("\0"), start, length, bool(attributes & ATTR_DIRECTORY)))
        # Children are offsets in 4-byte units from the start of this directory table, not from the entry.
        for child in (left, right):
            if child:
                pending.append(child * 4)
    return found


class Disc:
    """An Xbox 360 disc image, opened read-only."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._handle = open(self.path, "rb")
        try:
            self.base = game_partition(self._handle)
            found = _descriptor(self._handle, self.base + DESCRIPTOR)
            if found is None:  # (game_partition just proved there is one; be explicit for the type checker)
                raise IsoError("no XDVDFS volume found")
            self.root_sector, self.root_size = found
        except IsoError:
            self._handle.close()
            raise

    def __enter__(self) -> Disc:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._handle.close()

    @property
    def size(self) -> int:
        return _size(self._handle)

    def entries(self, path: str = "") -> list[Entry]:
        """The disc's files and directories under ``path`` (the root by default), each by full path."""
        return list(self._walk(path))

    def _walk(self, wanted: str):
        """Every entry on the disc whose path is ``wanted`` or lies under it (names compare case-insensitively:
        discs are not consistent about the case of ``Default.xex``)."""
        prefix = wanted.strip("/").lower()
        pending = [(self.root_sector, self.root_size, "", 0)]
        while pending:
            sector, size, parent, depth = pending.pop()
            if depth > MAX_DEPTH:
                raise IsoError("the disc's directories are nested deeper than any real one")
            for entry in _entries(self._handle, self.base, sector, size):
                path = f"{parent}{'/' if parent else ''}{entry.path}"
                if entry.directory:
                    pending.append((entry.sector, entry.size, path, depth + 1))
                lowered = path.lower()
                if not prefix or lowered == prefix or lowered.startswith(prefix + "/"):
                    yield Entry(path, entry.sector, entry.size, entry.directory)

    def find(self, path: str) -> Entry:
        """The entry at ``path``, or an IsoError naming what the disc does have at that level."""
        wanted = path.strip("/")
        for entry in self._walk(wanted):
            if entry.path.lower() == wanted.lower():
                return entry
        raise IsoError(f"{path!r} isn't on this disc: it has "
                       + ", ".join(e.path for e in self.entries(wanted.rpartition("/")[0])[:8]))

    def read(self, path: str) -> bytes:
        """A file's bytes."""
        entry = self.find(path)
        if entry.directory:
            raise IsoError(f"{path!r} is a directory")
        return _read_at(self._handle, self.base + entry.sector * SECTOR, entry.size)

    def boot_executable(self) -> bytes:
        """The game's own executable: the XEX2 at the disc root (``default.xex``)."""
        for name in ("default.xex", "Default.xex", "DEFAULT.XEX"):
            try:
                return self.read(name)
            except IsoError:
                continue
        raise IsoError("no default.xex at the disc root: this image isn't a bootable Xbox 360 game")


def extract_default_xex(image: Path | str, destination: Path) -> Path:
    """Write the disc's ``default.xex`` to ``destination`` (a file path) and return it."""
    with Disc(image) as disc:
        destination.write_bytes(disc.boot_executable())
    return destination


def main(argv: list[str] | None = None) -> int:
    """``python -m decompnet.consoles.xbox360.iso list <image>`` or ``extract <image> <file>``."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in ("list", "extract"):
        sys.stderr.write("usage: python -m decompnet.consoles.xbox360.iso list <image> | "
                         "extract <image> <default.xex destination>\n")
        return 2
    if args[0] == "list":
        with Disc(args[1]) as disc:
            for entry in disc.entries():
                kind = "dir " if entry.directory else "file"
                print(f"{kind} {entry.size:>12} {entry.path}")
        return 0
    if len(args) < 3:
        sys.stderr.write("extract needs a destination for default.xex\n")
        return 2
    written = extract_default_xex(args[1], Path(args[2]))
    print(f"wrote {written} ({written.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":  # pragma: no cover - the CLI entry point
    raise SystemExit(main())
