"""xorloser's XexTool, the user's own copy: it writes the decrypted image out of a retail XEX2.

A retail disc's executable can be encrypted, or compressed in a way decompdecomp refuses to touch (it never
decrypts game data itself). XexTool, run on the user's own disc, produces the base image, and that is enough to
decompile: the Xbox 360 console imports a PowerPC PE directly. Nothing is downloaded or pinned — this is someone
else's freeware — so Setup records where the user's copy is and the console runs it there under Wine.
"""

from __future__ import annotations

from pathlib import Path

from . import Kit, OwnCompiler, find

EXE = "xextool.exe"
VERSION = "xextool"  # the toolchain id the console asks for


class Xextool(Kit):
    id = VERSION
    name = "XexTool (your own copy)"
    platform = "Xbox 360"
    description = ("Writes a retail Xbox 360 executable's decrypted image out (`xextool -b`), for discs whose XEX2 "
                   "is encrypted or compressed in a way decompdecomp cannot read. Give the folder holding "
                   "xextool.exe; on Linux and macOS it runs under Wine.")
    files = (EXE,)

    def build(self, folder: Path) -> str | None:
        """XexTool's own version is in its banner, not its filename: nothing to read from the copy itself."""
        return None

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        """Expose xextool.exe as this kit's one tool, run through Wine like the XDK's cl.exe."""
        root = self.locate(folder)
        if root is None:
            return []
        exe = find(root, EXE)
        if exe is None:
            return []
        return [OwnCompiler(VERSION, "XexTool", exe, (), runner="wine", build=VERSION, root=root, args=(), flags=())]


XEXTOOL = Xextool()
