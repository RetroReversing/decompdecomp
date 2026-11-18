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

`files` requires only the compiler: the prefix and the SDK root are often separate folders. `locate()` accepts the
folder holding the compiler; the SDK is either in the same tree or named by XSDK_ROOT. When the SDK root itself is
given, XSDK_GCC may name the compiler prefix. The version comes from running `<gcc> -dumpversion` locally. Nothing
here needs a network or any SDK material of our own.
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

    def _compiler(self, folder: Path) -> Path | None:
        """The cross driver in `folder`, or the one named by XSDK_GCC when `folder` is the SDK root."""
        gcc = find(folder, GCC)
        if gcc is not None:
            return gcc
        given = os.environ.get("XSDK_GCC", "")
        at = Path(given).expanduser() if given else None
        return at if at is not None and at.is_file() else None

    def _sdk(self, folder: Path) -> Path | None:
        """The SDK root: the same tree when it holds the packer and link script, else XSDK_ROOT."""
        if all(find(folder, rel) is not None for rel in SDK_FILES):
            return folder
        given = os.environ.get("XSDK_ROOT", "")
        root = Path(given).expanduser() if given else None
        return root if root is not None and all(find(root, rel) is not None for rel in SDK_FILES) else None

    def locate(self, folder: Path) -> Path | None:
        try:
            gcc, sdk = self._compiler(folder), self._sdk(folder)
        except OSError:
            return None
        if gcc is None or sdk is None:
            # A compiler prefix on its own isn't enough: either the SDK files are in the same tree or XSDK_ROOT
            # points at them (a copy with no way to pack an ELF into XEX2 isn't a usable copy).
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
