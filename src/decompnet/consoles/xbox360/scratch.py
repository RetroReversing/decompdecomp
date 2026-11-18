"""Minimal XEX2 reader for the Xbox 360 console connector (stage 1).

Only the handful of header fields ``sniff()`` and ``game_info()`` need are read:
the magic, the payload/security offsets, the optional-header directory and the
image base. Nothing here validates a signature or loads the executable.

Format sources (documentation only, no code copied): the XEX2 header layout as
described in Xenia's ``src/xenia/kernel/util/xex2_info.h`` and as summarised by
Moonlight360's ``scripts/inspect-xex-contracts.py`` (GPL-3.0-or-later original
work). This module is an independent implementation from the documented offsets.

The image body at ``payload_offset`` is not necessarily the memory image: XEX2 may compress it (key 0x3FF,
``XEX_HEADER_FILE_FORMAT_INFO``) and may encrypt it. Only unencrypted images with no compression or XEX2 "basic"
compression (a table of data/zero block pairs) are expanded here, for Ghidra's disposable ELF wrapper. Encrypted
bodies and the normal/delta compression modes are NOT handled: decompdecomp does not decrypt game data.

For a basic-compressed XEX2 the raw ``payload_offset`` bytes are compressed, not the memory image Ghidra analyses.
``create`` therefore expands the image once and writes the EXPANDED bytes to ``orig/<version>.image.bin``; the
project judges against that file (offset 0), while the raw ``.xex`` is kept beside it for identity/provenance. For an
uncompressed XEX2 the raw file is the image and its ``payload_offset`` stays the base for file offsets.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ...forks.project import ForkError

MAGIC = b"XEX2"
MAX_DESCRIPTORS = 4096
TOOL_SECONDS = 3600
ERROR_TAIL = 1500

# optional-header directory keys (big-endian u32 key -> u32 value)
K_ENTRY = 0x10100
K_IMAGE_BASE = 0x10201
K_FILE_FORMAT = 0x3FF    # XEX_HEADER_FILE_FORMAT_INFO: offset of the encryption/compression descriptor
K_IMPORT_TABLE = 0x103FF  # a different key from K_FILE_FORMAT; the leading 1 is significant


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

    The raw key-to-value header directory is returned under ``headers`` so later stages can look up things this
    reader does not interpret (notably the file-format descriptor at key 0x3FF).
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
        "headers": selected,
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


# --------------------------------------------------------------------------------------------- function map / project
# Stage 3: a FUNCTION-MAP path that needs no Ghidra. XEX2 has no section table like an ELF's, so the "code" is
# approximated as one contiguous span from xex["image_base"] over xex["image_size"]. Addresses in functions.jsonl
# are runtime VAs (what a Ghidra probe reports); a VA's file offset is (va - image_base) + base_offset, where
# base_offset is 0 into an expanded image or xex["payload_offset"] into the raw file (see judge_image).
# Stage 4a: a GHIDRA-DISCOVERY path for games with no map, through a disposable ELF64/PPC64 wrapper below.

TEMPLATE = Path(__file__).with_name("project_template")
# The project's build tools, copied into a new project once by create() (shutil.copytree below). Derived from the
# template so a tool added to it later (e.g. ninja_syntax.py) is picked up too; on_start refreshes an existing
# project's copies of these, which is the fix for imported projects keeping stale tools forever.
TOOLS = tuple(f"tools/{p.name}" for p in sorted((TEMPLATE / "tools").glob("*.py")))
UNIT_BYTES = 0x1000  # a source file is a run of roughly this many bytes of neighbouring functions
GAP_BYTES = 0x400  # a hole this big between functions starts another source file
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

TYPES_H = """/* Basic types for this decompilation (PowerPC64, big-endian, the user's xsdk GCC).
 * Agents add structs and prototypes here or in new headers next to it. The game's functions are
 * func_<address>: write them with those names, as C, and the build gives them the original's symbols. */
#ifndef COMMON_H
#define COMMON_H

typedef signed char s8;
typedef unsigned char u8;
typedef signed short s16;
typedef unsigned short u16;
typedef signed int s32;
typedef unsigned int u32;
typedef signed long long s64;
typedef unsigned long long u64;
typedef float f32;
typedef double f64;
typedef int BOOL;
#define TRUE 1
#define FALSE 0
#ifndef NULL
#define NULL 0
#endif

#endif
"""


@dataclass
class Function:
    addr: int  # runtime VA
    offset: int  # file offset in the XEX2
    size: int
    name: str


def file_offset(va: int, image_base: int, base_offset: int) -> int:
    """The one VA -> file-offset conversion shared by the map and Ghidra paths.

    ``base_offset`` is 0 into an expanded (basic-compressed) image, or ``payload_offset`` into the raw file.
    """
    return va - image_base + base_offset


def map_functions(game: Path, info: dict, xex: dict, *, base_offset: int | None = None,
                  log=print) -> list[dict] | None:
    """Read the functions of the game from a ``functions.jsonl`` beside it, or return None if there is none.

    Each line is a JSON object with hex ``a`` (function VA), ``end`` (exclusive VA, hex string or int),
    optional ``n`` (name) and optional ``ranges`` (lists of ``[start, end]`` VAs). VAs are converted to file
    offsets with ``file_offset``: ``(va - image_base) + base_offset`` (``base_offset`` defaults to
    ``payload_offset`` for callers with no expanded image). ``addr`` and ``ranges`` stay in VA space; ``offset``
    is the file offset. This is the best available approximation for XEX2 (no ELF sections); see the note
    above. Bad lines are skipped, but ambiguous maps or extents outside the image are errors the user can act on.
    """
    base = xex.get("image_base")
    payload = xex.get("payload_offset") if base_offset is None else base_offset
    size = xex.get("image_size")
    if base is None or payload is None or size is None:
        raise ForkError("this XEX2 has no image base / payload offset: cannot map function addresses to file offsets")
    image_end = base + size

    # same candidate list as the PS4 connector, so a probe's output works unchanged
    candidates = [game.with_suffix(".functions.jsonl"), game.parent / "functions.jsonl",
                  game.parent / "functions" / "functions.jsonl"]
    extras = sorted(p for p in game.parent.iterdir() if p.is_file()
                    and (p.name.lower().endswith(".functions.jsonl") or p.name.lower() == "functions.jsonl"))
    if not any(p.is_file() for p in candidates):
        if len(extras) > 1:
            raise ForkError("more than one function map is beside this program: keep one, or name it functions.jsonl")
        candidates.extend(extras)

    for candidate in candidates:
        if not candidate.is_file():
            continue
        out: list[dict] = []
        skipped = 0
        dropped = 0
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                start = int(row["a"], 16) if isinstance(row["a"], str) else int(row["a"])
                end_value = row["end"]
                end = int(end_value, 16) if isinstance(end_value, str) else int(end_value)
            except (ValueError, KeyError, TypeError):
                skipped += 1
                continue  # tolerant: one bad line is not a reason to reject the whole map
            if start < base or end <= start:
                raise ForkError(f"{candidate}: functions must use runtime VAs at or above 0x{base:X}, "
                                "with an exclusive end after the start")
            # objdiff measures PPC64 code length in whole 4-byte instructions; Ghidra emits 1-byte
            # placeholder stubs that are not real functions. Drop sub-instruction extents instead of
            # feeding them to objdiff (which fails with "Code length must be a multiple of 4").
            if end - start < 4:
                dropped += 1
                continue
            if end > image_end:
                raise ForkError(f"{candidate}: function extent 0x{start:X}-0x{end:X} is outside the image "
                                f"(0x{base:X}-0x{image_end:X})")
            offset = file_offset(start, base, payload)
            if offset + (end - start) > payload + size:
                raise ForkError(f"{candidate}: function extent maps outside the XEX2 image")
            try:
                ranges = []
                for pair in row.get("ranges", []):
                    lo, hi = (int(v, 16) if isinstance(v, str) else int(v) for v in pair)
                    if lo < base or hi <= lo or hi > image_end:
                        raise ValueError("range outside image")
                    ranges.append([lo, hi])
            except (ValueError, TypeError, OverflowError):
                raise ForkError(f"{candidate}: invalid function body ranges; use [start, end] VAs inside the image") from None
            out.append({"name": row.get("n") or "", "addr": start, "offset": offset,
                        "size": end - start, "ranges": ranges})
        if skipped:
            log(f"{candidate}: skipped {skipped} malformed line(s)")
        if dropped:
            log(f"{candidate}: dropped {dropped} stub(s) with size < 4 bytes")
        if out:
            return out
    return None


# Stage 4a: Ghidra discovery for a game with no functions.jsonl. A real XEX2 is not an ELF, but Ghidra loads
# ELF64/PPC64 natively (language PowerPC:BE:64:A2ALT-32addr), so we wrap the image in a throwaway ET_EXEC whose one
# PT_LOAD maps the image bytes at image_base. This is a DISPOSABLE INPUT FOR GHIDRA ONLY: it is not a runnable program
# and is never kept. The live Ghidra analysis cannot be exercised here (Ghidra is not installed in this environment);
# the wrapper's bytes and the VA->file-offset conversion are covered by synthetic tests in tests/test_xbox360_ghidra.py.

ET_EXEC, PT_LOAD, PF_X, PF_R = 2, 1, 0x1, 0x4  # standard ELF values, independent of the delink tool's ET_REL
EM_PPC64 = 21
ELF_HEADER_BYTES, PHDR_BYTES = 64, 56


def read_file_format(data: bytes, xex: dict) -> dict:
    """Read the XEX2 file-format descriptor (header key 0x3FF) out of the raw image bytes.

    The descriptor at the key's value offset is ``>IHH`` (info_size, encryption_type, compression_type). For basic
    compression (1) it is followed by ``(info_size - 8) / 8`` pairs of big-endian ``>II`` (data_size, zero_size)
    block lengths. A missing descriptor means an unencrypted, uncompressed image; hand-built xex dicts in tests
    may have no ``headers`` field at all.
    """
    fmt: dict = {"encryption": 0, "compression": 0, "blocks": []}
    at = (xex.get("headers") or {}).get(K_FILE_FORMAT)
    if at is None:
        return fmt
    if at + 8 > len(data):
        raise ForkError("this XEX2's file-format descriptor is truncated: cannot tell how its image is stored")
    info_size, encryption, compression = struct.unpack_from(">IHH", data, at)
    if info_size < 8 or (info_size - 8) % 8 or at + info_size > len(data):
        raise ForkError("this XEX2's file-format descriptor is implausible: cannot tell how its image is stored")
    fmt["encryption"] = encryption
    fmt["compression"] = compression
    if compression == 1:
        fmt["blocks"] = [struct.unpack_from(">II", data, off) for off in range(at + 8, at + info_size, 8)]
    return fmt


def expand_basic_image(data: bytes, xex: dict, fmt: dict) -> bytes:
    """Return the decompressed memory image behind ``data``, ready to map in the Ghidra wrapper.

    With no compression the bytes at ``payload_offset`` already are the image. With basic compression each block
    contributes ``data_size`` bytes read from a cursor starting at ``payload_offset``, then ``zero_size`` zero
    bytes; the concatenation must be exactly ``image_size`` bytes long. Encrypted bodies and the normal/delta
    compression modes are refused: decompdecomp does not decrypt game data.
    """
    payload, size = xex.get("payload_offset"), xex.get("image_size")
    if payload is None or size is None or size <= 0:
        raise ForkError("this XEX2 has no payload offset / image size: cannot expand its image")
    if fmt.get("encryption"):
        raise ForkError("this XEX2's image body is encrypted, and decompdecomp cannot decrypt it here. Prepare an "
                        "unencrypted copy with your own tools (e.g. xexTool), or supply a functions.jsonl map")
    compression = fmt.get("compression", 0)
    if compression not in (0, 1):
        raise ForkError(f"this XEX2's image body uses compression type {compression} (normal/delta), which "
                        "decompdecomp cannot expand here. Prepare an unencrypted basic-compression copy with your "
                        "own tools (e.g. xexTool or prepare-xex-basic.py), or supply a functions.jsonl map")

    if compression == 0:
        image = data[payload:payload + size]
        if len(image) != size:
            raise ForkError(f"this XEX2 image is truncated: expected {size} bytes at offset 0x{payload:X}, "
                            f"found {len(image)}")
        return image

    cursor = payload
    out = bytearray()
    for data_size, zero_size in fmt.get("blocks", []):
        if data_size + zero_size > size - len(out):
            raise ForkError(f"this XEX2 basic-compression block table expands past the declared image size "
                            f"of {size} bytes")
        chunk = data[cursor:cursor + data_size]
        if len(chunk) != data_size:
            raise ForkError(f"this XEX2 image is truncated: expected {data_size} more bytes at offset "
                            f"0x{cursor:X}, found {len(chunk)}")
        out += chunk
        out += bytes(zero_size)
        cursor += data_size
    if len(out) != size:
        raise ForkError(f"this XEX2 basic-compression block table expands to {len(out)} bytes, but the image "
                        f"header says {size}")
    return bytes(out)


def ghidra_image(game: Path, xex: dict, tmp: Path) -> Path:
    """Build a minimal big-endian ELF64/PPC64 ET_EXEC wrapping the XEX2 image, at ``tmp``; return its path.

    The 64-byte ELF header and single 56-byte PT_LOAD program header are followed immediately by the expanded
    memory image at file offset 64 + 56. The segment maps it (basic-compressed bodies are decompressed first) at
    ``image_base`` and ``e_entry`` is the XEX2 entry point (or the image base when the header has none). Ghidra
    reads this as a normal ELF and finds functions in the whole executable image. No section headers: the image
    is one contiguous span, like stage 3's ``text``. This is a disposable wrapper for Ghidra, not a runnable game;
    encrypted and normal/delta-compressed bodies are refused (see ``expand_basic_image``).
    """
    base = xex.get("image_base")
    if base is None:
        raise ForkError("this XEX2 has no image base: cannot build a Ghidra wrapper")
    data = game.read_bytes()
    image = expand_basic_image(data, xex, read_file_format(data, xex))
    mapped = len(image)  # the PT_LOAD covers the expanded image, which is exactly image_size bytes
    ident = b"\x7fELF" + bytes([2, 2, 1, 0]) + bytes(8)  # ELFCLASS64, ELFDATA2MSB, EV_CURRENT, SYSV ABI
    phoff = ELF_HEADER_BYTES
    image_off = phoff + PHDR_BYTES
    header = struct.pack(">16sHHIQQQIHHHHHH", ident, ET_EXEC, EM_PPC64, 1,
                         xex.get("entry_point") or base, phoff, 0, 0,
                         ELF_HEADER_BYTES, PHDR_BYTES, 1, 0, 0, 0)
    # p_align=1: no alignment requirement, so p_offset (120) need not match image_base modulo a page size.
    phdr = struct.pack(">IIQQQQQQ", PT_LOAD, PF_X | PF_R, image_off, base, base, mapped, mapped, 1)
    target = Path(tmp) / "xex2-ppc64.elf"
    target.write_bytes(header + phdr + image)
    return target


def _base_offset(xex: dict, fmt: dict) -> int:
    """0 into a basic-compressed image's expanded bytes, else the raw file's payload offset."""
    return 0 if fmt.get("compression") == 1 else xex["payload_offset"]


def judge_image(root: Path, game: Path, xex: dict, fmt: dict, version: str) -> tuple[str, str, int]:
    """Pick the image the project judges against: (config-relative path, sha1, base offset for VAs).

    For a basic-compressed XEX2 the raw payload is compressed while Ghidra analysed the EXPANDED image, so target
    objects must be cut from the expanded bytes; write them once to ``orig/<version>.image.bin`` and use offset 0.
    The raw ``.xex`` is still kept in ``orig/`` for identity (copied by ``create``). For an uncompressed body the raw
    file at ``payload_offset`` already is the image, so that stays the base. ``text.size`` is ``image_size`` either
    way (``expand_basic_image`` guarantees the expanded length equals it).
    """
    offset = _base_offset(xex, fmt)
    if fmt.get("compression") == 1:
        image = expand_basic_image(game.read_bytes(), xex, fmt)
        rel = f"orig/{version}.image.bin"
        (root / rel).write_bytes(image)
        return rel, hashlib.sha1(image).hexdigest(), offset
    return f"orig/{version}.xex", hashlib.sha1(game.read_bytes()).hexdigest(), offset


def _function_from_ghidra(row: dict, xex: dict, base_offset: int | None = None) -> dict | None:
    """One Ghidra function row -> the dict shape ``map_functions`` returns, or None to drop it.

    Ghidra reports VAs in ``entry``/``end`` (hex strings or ints); a VA's file offset is ``file_offset``:
    ``(va - image_base) + base_offset`` (defaulting to ``payload_offset`` without an expanded image). Thunks,
    sub-instruction stubs and extents that leave the mapped image are dropped: Ghidra also sees stubs and helpers
    outside this XEX2's image.
    """
    if row.get("thunk"):
        return None
    base, size = xex["image_base"], xex["image_size"]
    payload = xex.get("payload_offset") if base_offset is None else base_offset
    try:
        entry = int(row["entry"], 16) if isinstance(row["entry"], str) else int(row["entry"])
        end = int(row["end"], 16) if isinstance(row["end"], str) else int(row["end"])
    except (KeyError, ValueError, TypeError):
        return None
    # PPC64 instructions are 4 bytes and objdiff requires the code length to be a multiple of 4;
    # Ghidra emits 1-byte placeholder stubs that are not real functions, so drop sub-instruction extents.
    if end - entry < 4 or entry < base or end > base + size:
        return None
    return {"name": row.get("name") or "", "addr": entry, "offset": file_offset(entry, base, payload),
            "size": end - entry, "ranges": [[entry, end]]}


def ghidra_functions(game: Path, xex: dict, *, base_offset: int | None = None, log=print) -> list[dict] | None:
    """Find the game's functions with Ghidra, for a game with no functions.jsonl map.

    Builds a disposable ELF64/PPC64 wrapper of the XEX2 image, asks the shared ``ghidra.functions`` for every
    function it finds, and returns them in the same shape as ``map_functions`` (VA ``addr``, file ``offset``).
    Conventions are not required here: PowerPC64 has a fixed ABI, so Ghidra never decorates its names and "unknown"
    is expected. Returns None when nothing usable is found. The live Ghidra call cannot be verified here (Ghidra is
    not installed in this environment): only the wrapper and conversion are unit-tested.
    """
    from ...forks import ghidra

    if not ghidra.available():
        raise ForkError("Ghidra isn't installed: install it in Setup to find this game's functions, or put a "
                        "functions.jsonl map beside the game (one {\"a\", \"end\", \"n\"} object per line)")
    with tempfile.TemporaryDirectory(prefix="decomp-xbox360-") as tmp:
        wrapper = ghidra_image(game, xex, Path(tmp))
        log(f"analysing {game.name} with Ghidra through a disposable ELF64/PPC64 wrapper (no function map)")
        rows = ghidra.functions(wrapper, require_conventions=False)
    found = [row for row in (_function_from_ghidra(r, xex, base_offset) for r in rows) if row is not None]
    return found or None


def units(functions: list[Function]) -> dict[str, list[Function]]:
    """Source files: runs of neighbouring functions of about UNIT_BYTES each."""
    out: dict[str, list[Function]] = {}
    cur: list[Function] = []
    for fn in functions:
        if cur and (fn.addr + fn.size - cur[0].addr > UNIT_BYTES
                    or fn.addr - (cur[-1].addr + cur[-1].size) > GAP_BYTES):
            out[f"text_{cur[0].addr:08X}"] = cur
            cur = []
        cur.append(fn)
    if cur:
        out[f"text_{cur[0].addr:08X}"] = cur
    return out


def write_atomic(path: Path, text: str) -> None:
    """Write beside the file and rename: the app and agents can act on the same project at once."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def objdiff_config(version: str, groups: dict[str, list[Function]]) -> dict:
    """The initial objdiff.json, so the project is detectable and judgeable even before the first kit build.

    The target objects are delinked from the XEX2 at import time (no xsdk kit needed). ``tools/configure.py``
    writes the same file again, so this stays a faithful fallback, with the same conventional paths.
    """
    build = Path("build") / version
    return {
        "$schema": "https://raw.githubusercontent.com/encounter/objdiff/main/config.schema.json",
        "custom_make": "ninja", "custom_args": [], "build_target": False, "build_base": True,
        "watch_patterns": ["*.c", "*.h"],
        "options": {"functionRelocDiffs": "all", "combineTextSections": True},
        "units": [{"name": u,
                   "target_path": str(build / "target" / f"{u}.o"),
                   "base_path": str(build / "src" / "auto" / f"{u}.o"),
                   "metadata": {"source_path": f"src/auto/{u}.c", "complete": False}}
                  for u in groups],
    }


def _run(cmd: list[str], root: Path, what: str, log) -> None:
    """Run a build command in the project, raising an actionable ForkError (with the tool's tail) on failure."""
    from ...forks.project import ForkProject

    ok, tail = ForkProject(root).run(cmd, log=log, timeout=TOOL_SECONDS)
    if not ok:
        raise ForkError(f"{what} failed:\n{tail[-ERROR_TAIL:]}")


def _ninja() -> str:
    """The ninja bundled next to this interpreter, or the one on PATH; fail clearly when there is none."""
    bundled = Path(sys.executable).with_name("ninja")
    if bundled.is_file():
        return str(bundled)
    found = shutil.which("ninja")
    if found:
        return found
    raise ForkError("ninja is needed to delink the project's target objects; install it and try again")


def tools_version() -> str:
    """A short hash of the template's build tools. A project's manifest records it, and Xbox360.on_start compares
    it, so a change to these tools reaches every existing project once (the fix for imported projects never
    receiving tool updates)."""
    digest = hashlib.sha1()
    for rel in TOOLS:
        src = TEMPLATE / rel  # = Path(__file__).parent / "project_template" / rel
        digest.update(rel.encode() + b"\0" + src.read_bytes())
    return digest.hexdigest()[:12]


def write_tools(root: Path) -> list[str]:
    """Bring an existing project's build tools up to the current template (the PC scratch.write_tools pattern).

    An imported project copies the template once (shutil.copytree in create), so its tools/configure.py and
    tools/compile.py stay stale: the old configure.py only asked kits.compilers(["xsdk"]) and had no runner, so
    re-running it always re-selected the free xsdk GCC even after the user registered a real XDK. Each tool file is
    replaced when its content differs; none of them holds per-project settings (the game's flags live in the
    environment/config, not here). Returns the tool paths that changed, for the caller's log line."""
    changed = []
    for rel in TOOLS:
        src = TEMPLATE / rel
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)  # older projects have tools/, but don't assume it
        text = src.read_text(encoding="utf-8")
        if not dest.exists() or dest.read_text(encoding="utf-8") != text:
            dest.write_text(text, encoding="utf-8")
            changed.append(rel)
    return changed


def create(root: Path, game: Path, *, compiler: str | None = None, log=print) -> dict:
    """Build a PROJECT-mode decomp project in ``root`` from a function map beside ``game``, or from Ghidra.

    A ``functions.jsonl`` map supplies every function when present; otherwise Ghidra discovers them through a
    disposable ELF64/PPC64 wrapper of the image (stage 4a). XEX2 has no ELF sections, so the config's ``text`` is
    the whole image, and no relocations are recovered here (relocs are written empty). For a basic-compressed XEX2
    the project judges against the EXPANDED image written to ``orig/<version>.image.bin`` (offset 0); the raw
    ``.xex`` is kept beside it for identity and an uncompressed body is judged there at its payload offset.
    Encrypted and normal/delta-compressed bodies are not handled, so use a function map (or prepare an unencrypted
    basic-compression copy) for those. ``root`` must not exist yet. Creating the project delinks only the target
    objects from the game (no xsdk kit needed); compiling the candidate objects needs the user's xsdk kit and is
    left to the user's first real ``ninja prepare`` build.
    """
    info = game_info(game)
    try:
        data = game.read_bytes()
        xex = read_xex2(data)
    except (ValueError, OSError) as exc:
        raise ForkError(f"{game} is not a readable XEX2 program: {exc}") from None
    if xex["image_base"] is None:
        raise ForkError("this XEX2 has no image base, so function addresses cannot be mapped to file offsets")
    fmt = read_file_format(data, xex)
    # Ghidra analysed the expanded image of a basic-compressed XEX2, so a VA's file offset starts at 0 in that image;
    # the raw payload offset is the base only for an uncompressed body.
    base_offset = _base_offset(xex, fmt)

    version = re.sub(r"[^A-Za-z0-9_.-]+", "_", info["id"]) or "game"
    chosen = compiler or "xbox360-xsdk-placeholder"
    log(f"reading {game.name}: image at 0x{xex['image_base']:X} ({xex['image_size']} bytes)")
    found = map_functions(game, info, xex, base_offset=base_offset, log=log)
    source = "function map"
    if found:
        log(f"using the function map beside the game: {len(found)} functions")
    else:
        from ...forks import ghidra

        if not ghidra.available():
            raise ForkError("no functions.jsonl map beside the game and no Ghidra to find them: put a map in the "
                            "game's folder (one JSON object per line: {\"a\": \"<VA hex>\", \"end\": \"<VA hex>\", "
                            "\"n\": \"name\"}; VAs are runtime addresses), or install Ghidra in Setup")
        source = "Ghidra analysis"
        found = ghidra_functions(game, xex, base_offset=base_offset, log=log)
        if not found:
            raise ForkError("neither a functions.jsonl map nor Ghidra found functions in this XEX2 image")

    functions: list[Function] = []
    used: set[str] = set()
    dropped = 0
    for row in found:
        # Defensive: both producers drop sub-instruction stubs already, but a hand-edited map could still
        # slip one in. objdiff cannot measure PPC64 code shorter than 4 bytes; never round the size up.
        if row["size"] < 4:
            dropped += 1
            continue
        name = row["name"]
        if not (name and _IDENT.fullmatch(name)) or name in used:
            name = f"func_{row['addr']:08X}"
        used.add(name)
        functions.append(Function(row["addr"], row["offset"], row["size"], name))
    if dropped:
        log(f"dropped {dropped} stub(s) with size < 4 bytes")
    groups = units(functions)
    log(f"{len(functions)} functions in {len(groups)} source files")

    shutil.copytree(TEMPLATE, root)
    (root / "orig").mkdir()
    original = root / "orig" / f"{version}.xex"
    try:
        os.link(game, original)  # hardlink where possible, never a second copy of a large game
    except OSError:
        shutil.copyfile(game, original)
    judge_rel, judge_sha1, judge_offset = judge_image(root, game, xex, fmt, version)
    (root / "include").mkdir(exist_ok=True)
    (root / "include" / "common.h").write_text(TYPES_H, encoding="utf-8")

    conf = root / "config" / version
    conf.mkdir(parents=True)
    write_atomic(conf / "functions.json", json.dumps({
        "target_format_version": 4, "elf": judge_rel, "sha1": judge_sha1,
        "image_base": xex["image_base"], "entry": xex.get("entry_point") or 0,
        # XEX2 approximation: the "text" is the whole image, not an ELF section. For a basic-compressed XEX2 this is
        # the expanded image in orig/ (offset 0); otherwise it is the raw payload.
        "text": {"va": xex["image_base"], "offset": judge_offset, "size": xex["image_size"]},
        "units": {u: [{"name": f.name, "addr": f.addr, "offset": f.offset, "size": f.size, "relocs": []}
                      for f in fns] for u, fns in groups.items()},
    }, indent=1))
    for u, fns in groups.items():
        src = root / "src" / "auto" / f"{u}.c"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(f"/* {len(fns)} function{'s' if len(fns) != 1 else ''} from 0x{fns[0].addr:08X} to "
                       f"0x{fns[-1].addr + fns[-1].size:08X}, from the game's {source}. */\n"
                       "#include \"common.h\"\n", encoding="utf-8")
    write_atomic(root / "objdiff.json", json.dumps(objdiff_config(version, groups), indent=2))

    # Only the target objects are built here: delinking reads this project's image and functions.json (stdlib
    # only), while compiling the candidate objects needs the user's xsdk kit, which may not be set up yet. The
    # project's default `ninja prepare` builds both once the kit is available; import runs `prepare-targets`.
    _run([sys.executable, "tools/configure.py", version], root, "the first build", log)
    _run([_ninja(), "prepare-targets"], root, "the delink targets", log)
    if not (root / "objdiff.json").exists():
        raise ForkError("the first build finished without writing objdiff.json")
    targets = [root / "build" / version / "target" / f"{u}.o" for u in groups]
    missing = [str(t.relative_to(root)) for t in targets if not t.is_file()]
    if missing:
        raise ForkError("delink finished without writing every target object (e.g. "
                        f"{', '.join(missing[:3])}); check config/{version}/functions.json and tools/delink.py")

    (root / ".decomp.wtf.json").write_text(json.dumps({
        "created": "from scratch", "platform": "xbox360", "version": version, "compiler": chosen,
        "game": {"what": "your own copy of the game's XEX2", "path": f"orig/{version}.xex", "sha1": info["sha1"]},
        "setup": [["python", "tools/configure.py", version], ["ninja", "prepare"]],
    }, indent=2), encoding="utf-8")
    (root / ".gitignore").write_text("orig/\nbuild/\nbuild.ninja\n.ninja_*\nobjdiff.json\n__pycache__/\n.decomp-wtf/\n",
                                     encoding="utf-8")
    (root / "README.md").write_text(
        f"# {info['title']} decompilation\n\n"
        "Started from scratch with decompdecomp: functions were found by Ghidra or read from a `functions.jsonl` "
        f"map beside your own XEX2 copy ({source}), and objdiff compares each source file with the original.\n\n"
        f"1. Your copy of the program is here as `orig/{version}.xex` (never commit it).\n"
        f"2. Install your xsdk kit, then `XSDK_CC=powerpc-xsdk-elf-gcc python tools/configure.py {version}` and "
        "`ninja`. See `project_template/README` for the flags.\n"
        "3. Decompiled code goes in `src/`; `objdiff.json` shows each function's progress.\n\n"
        "At import time only the target objects were delinked from your XEX2 (that needs no xsdk kit). Compiling "
        "the candidate objects needs your kit, so run step 2 for the full build once it is installed.\n\n"
        "XEX2 has no ELF sections: `text` here is the whole image, addresses are runtime VAs, and no relocations "
        "are recovered by this stage. For a basic-compressed XEX2 the project judges against the expanded image "
        f"in `orig/{version}.image.bin` (the raw `{version}.xex` is kept for identity); for an uncompressed one it "
        "judges against the raw file at its payload offset. When there was no function map, Ghidra found the "
        "functions through a disposable ELF64/PPC64 wrapper of the image.\n",
        encoding="utf-8")
    log(f"{len(groups)} source files for agents to write into")
    return {"platform": "xbox360", "id": version, "title": info["title"], "compiler": chosen,
            "sources": len(groups), "sha1": info["sha1"]}
