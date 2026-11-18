"""Build a small ORIGINAL Xbox 360 demo (demo.c) into a XEX2 with the user's xsdk copy.

This is a scaffold, not a CI fixture: the xsdk toolchain and SDK are a user-supplied kit
(~1.5 GB, no pinned archive). XSDK_GCC names the cross compiler's prefix and XSDK_ROOT the
SDK repository root; both default to the documented paths on the machine this was written
on. When either is missing (or the SDK hasn't been `make`d), the script says so and stops
instead of pretending to have built a game. The commands are the ones xsdk's README and
the local Xbox 360 porting notes record: compile with the target flags, link -static
-nostdlib -T src/xsdk.ld against libxsdk.a, then pack the ELF with tools/elf2xex.py.
No SDK, compiler or game data is vendored here.

    python3 projects/demo-xbox360/build.py   ->  build/demo.xex (+ build/demo.json, build/symbols.map)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from decompnet.limits import capped

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"

# Documented defaults; the env vars let the same script work wherever the user put their copy.
GCC_DEFAULT = "/home/phil/xsdk-gcc/bin/powerpc-xsdk-elf-gcc"
SDK_DEFAULT = "/home/phil/Projects/xsdk"

# xsdk's recorded target flags (the C++-only -fno-exceptions/-fno-rtti don't apply to this C demo).
CFLAGS = ("-O2", "-mcpu=cell", "-fno-pic", "-mcall-xsdk", "-ffreestanding", "-fno-builtin")
LDFLAGS = ("-static", "-nostdlib")


def tool_paths() -> tuple[Path, Path]:
    """The user's cross compiler and SDK root, or a clear message about what is missing."""
    import os

    gcc = Path(os.environ.get("XSDK_GCC", GCC_DEFAULT)).expanduser()
    sdk = Path(os.environ.get("XSDK_ROOT", SDK_DEFAULT)).expanduser()
    if not gcc.is_file():
        raise SystemExit(f"xsdk compiler not found at {gcc}: set XSDK_GCC to your "
                         "bin/powerpc-xsdk-elf-gcc and set up the xsdk kit in Compilers & tools")
    if not sdk.is_dir():
        raise SystemExit(f"xsdk SDK root not found at {sdk}: set XSDK_ROOT to your xsdk checkout")
    return gcc, sdk


def write_key(game: Path, elf: Path) -> None:
    """build/demo.json and build/symbols.map: the game made and each function's address, for conformance."""
    from elftools.elf.elffile import ELFFile

    with elf.open("rb") as stream:
        symbols = {s.name: s["st_value"] for s in ELFFile(stream).get_section_by_name(".symtab").iter_symbols()
                   if s.name and s["st_info"]["type"] == "STT_FUNC" and s["st_size"]}
    (BUILD / "demo.json").write_text(json.dumps({"game": game.name, "symbols": symbols}, indent=2), encoding="utf-8")
    (BUILD / "symbols.map").write_text("".join(f"{addr:x} {name}\n" for name, addr in sorted(symbols.items())),
                                       encoding="utf-8")


def main() -> None:
    gcc, sdk = tool_paths()
    link_script, packer, library = sdk / "src" / "xsdk.ld", sdk / "tools" / "elf2xex.py", sdk / "build" / "libxsdk.a"
    missing = [str(p) for p in (link_script, packer, library) if not p.is_file()]
    if missing:
        raise SystemExit(f"xsdk is incomplete: missing {', '.join(missing)}; run `make` in the SDK root first")
    BUILD.mkdir(exist_ok=True)
    obj, elf, game = BUILD / "demo.o", BUILD / "demo.elf", BUILD / "demo.xex"
    for cmd in ([str(gcc), *CFLAGS, "-c", str(HERE / "demo.c"), "-o", str(obj)],
                [str(gcc), *LDFLAGS, f"-T{link_script}", str(obj), str(library), "-o", str(elf)],
                [sys.executable, str(packer), str(elf), str(game)]):
        subprocess.run(capped(cmd), check=True, timeout=120)
    write_key(game, elf)
    print(f"built {game.relative_to(HERE)} from demo.c; no game data, no third-party code")


if __name__ == "__main__":
    main()
