"""xenosworks xsdk: the free, from-scratch Xbox 360 SDK and its GCC 15 cross compiler, the user's own copy.

xsdk is a public MIT-licensed project (github.com/xenosworks/xsdk), but nothing in it is published as a decompdecomp
pack: the GCC/binutils/newlib cross toolchain and the SDK checkout are about 1.5 GB with no pinned archive. Setup
records where the user's copy is; this kit reads it there and never copies, downloads or vendors any of it.

The layout is the one the xsdk README documents ("Building" and "Layout"): the toolchain is installed at ~/xsdk-gcc,
so its driver is <prefix>/bin/powerpc-xsdk-elf-gcc, a native Linux program; the SDK repository root holds
src/xsdk.ld (the link script) and tools/elf2xex.py (the XEX2 packer), with include/xsdk/ and src/ beside them. A
title is compiled with -mcpu=cell -fno-pic -mcall-xsdk -ffreestanding, linked -static -nostdlib -T src/xsdk.ld
against libxsdk.a, then packed with python3 tools/elf2xex.py <elf> <out.xex>. Those are recorded build commands
(documented by xsdk's README and the local Xbox 360 porting notes), not a claim about booting real hardware.

`files` requires only the compiler: the toolchain prefix and the SDK checkout are often separate folders. `locate()`
accepts a folder if it yields both halves: the compiler is `<folder>/bin/...`, XSDK_GCC, or (for an SDK root) the
common `~/xsdk-gcc` prefix; the SDK root is `<folder>` itself, XSDK_ROOT, or a common nearby location
(`<folder>.parent/Projects/xsdk`, `<folder>.parent/xsdk`, `~/Projects/xsdk`, `<folder>/xsdk`). That covers the usual
split where the two trees sit side by side. The version comes from running `<gcc> -dumpversion` locally. Nothing here
needs a network or any SDK material of our own.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import Kit, OwnCompiler, find

GCC = "bin/powerpc-xsdk-elf-gcc"
SDK_FILES = ("tools/elf2xex.py", "src/xsdk.ld")  # (optional beside the compiler: the SDK root's packer and link script)
FLAGS = ("-O2", "-mcpu=cell", "-fno-pic", "-mcall-xsdk")  # (xsdk's documented target flags)


class Xsdk(Kit):
    id = "xsdk"
    name = "xenosworks xsdk (your own copy)"
    platform = "Xbox 360"
    license = "MIT"  # (xsdk's own licence: the user supplies the copy, nothing is downloaded)
    description = ("The free, from-scratch Xbox 360 SDK and its GCC 15 cross compiler. Give the folder of your own "
                   "copy: the toolchain prefix holding bin/powerpc-xsdk-elf-gcc, or a checkout holding the SDK's "
                   "tools/ and src/ too.")
    files = (GCC,)
    places = ()

    def _has_sdk(self, root: Path) -> bool:
        """Whether `root` holds both SDK files: the XEX2 packer and the link script."""
        return all(find(root, rel) is not None for rel in SDK_FILES)

    def sdk_root(self, folder: Path) -> Path | None:
        """Find the SDK root as a pure filesystem search: the folder, XSDK_ROOT, then common nearby locations.

        No network and no SDK material of our own: these are just the places a user's checkout commonly lives, so
        pointing Setup at either half of a split layout (compiler prefix or SDK checkout) is enough.
        """
        candidates = [folder]
        given = os.environ.get("XSDK_ROOT", "")
        if given:
            candidates.append(Path(given).expanduser())
        candidates.extend((
            folder.parent / "Projects" / "xsdk",
            folder.parent / "xsdk",
            Path.home() / "Projects" / "xsdk",
            folder / "xsdk",
        ))
        for root in candidates:
            if self._has_sdk(root):
                return root
        return None

    def _sdk(self, folder: Path) -> Path | None:
        """The SDK root found by `sdk_root` (the internal name kept by verify/locate)."""
        return self.sdk_root(folder)

    def _compiler(self, folder: Path) -> Path | None:
        """The cross driver in `folder`, XSDK_GCC, or the common ~/xsdk-gcc prefix when `folder` is an SDK root."""
        gcc = find(folder, GCC)
        if gcc is not None:
            return gcc
        given = os.environ.get("XSDK_GCC", "")
        if given:
            at = Path(given).expanduser()
            if at.is_file():
                return at
        if self._has_sdk(folder):  # an SDK checkout: its toolchain prefix is often the ~/xsdk-gcc sibling
            at = Path.home() / "xsdk-gcc" / GCC
            if at.is_file():
                return at
        return None

    def locate(self, folder: Path) -> Path | None:
        try:
            gcc, sdk = self._compiler(folder), self._sdk(folder)
        except OSError:
            return None
        if gcc is None or sdk is None:
            # A prefix or a checkout on its own isn't enough: the other half must be found (same tree, env var, or
            # a common nearby location). A copy with no way to pack an ELF into XEX2 isn't a usable copy.
            return None
        return folder.resolve()

    def build(self, folder: Path) -> str | None:
        """GCC's own version ("15.3.0"), read from the user's native driver; None when it can't be run here."""
        gcc = self._compiler(folder)
        if gcc is None:
            return None
        try:
            result = subprocess.run([str(gcc), "-dumpversion"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip().splitlines()[-1] or None

    def verify(self, folder: Path) -> str:
        """An honest sentence for Setup: the version the driver reports and where the SDK files were found."""
        version = self.build(folder) or "a version it cannot read"
        sdk = self._sdk(folder)
        return f"xsdk GCC {version}" + ("" if sdk else "; SDK files not found (set XSDK_ROOT)")

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        gcc = self._compiler(folder)
        if gcc is None:
            return []
        version = self.build(folder) or "unknown"
        tag = re.sub(r"[^a-z0-9.]+", "-", version.lower())
        return [OwnCompiler(
            f"xsdk-{tag}", f"xsdk GCC {version} (your copy)", gcc, (), runner="",  # a native Linux program
            build=version, root=gcc.parent.parent, args=("-c",), flags=FLAGS,
        )]


XSDK = Xsdk()
