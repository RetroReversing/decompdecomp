"""Microsoft's Xbox 360 XDK, the user's own copy: the real PowerPC C/C++ compiler retail Xbox 360 games were built
with (cl.exe 16.00.x, run under Wine on Linux and macOS) and, in the fuller copies, the xbox/include headers and
xbox/lib libraries. Unlike xsdk (a free, from-scratch toolchain), this is Microsoft's proprietary SDK: nothing is
downloaded or pinned. Setup records where the user's copy is and this kit reads it there.

What is verified: all four XDK builds the user extracted (11511, 20353, 21119, 21256.3) hold
XDK/bin/win32/cl.exe with a PE version resource of 16.00.10224.00 (11511) or 16.00.11886.00 (the other three),
and each cl.exe answers `/?` under wine-11.21 (Staging). What is NOT claimed: that a compile through Wine
reproduces a real game's bytes. That needs the copy's headers and libraries plus the game's own flags, and is left
to the project's build; this kit only exposes the compiler and the folders it found.
"""

from __future__ import annotations

from pathlib import Path

from . import Kit, OwnCompiler, file_version, find

CL = "bin/win32/cl.exe"
C1 = "bin/win32/c1.dll"
VERSION = "xdk360"  # the compiler id prefix in a project's build (xdk360-20353)


class Xdk360(Kit):
    id = "xdk360"
    name = "Microsoft Xbox 360 XDK (your own copy)"
    platform = "Xbox 360"
    description = ("The real Microsoft PowerPC C/C++ compiler retail Xbox 360 games were built with. Give the "
                   "folder of your own copy (the one holding XDK/bin/win32/cl.exe, or the XDK folder inside it); "
                   "on Linux and macOS it runs under Wine.")
    files = (CL,)
    places = ("XDK",)  # some copies unpack as <build>/XDK/bin/win32, others are given as the XDK folder itself
    versions = (VERSION,)
    known_files = (C1,)
    known = {  # SHA-256 of c1.dll, read from the user's four extracted builds
        "efdba0fdb4ac26840b07c1fc6b84e9cbdec6f0874f837a5c8faf3bd893a51260":
            "XDK 11511's c1.dll (MSVC 16.00.10224.00)",
        "68949991f05c5a26c43c113f8742cae201aedc90b659c949098cdb73c6ec8ef5":
            "XDK 20353/21119/21256.3's c1.dll (MSVC 16.00.11886.00; the same code generator in all three)",
    }

    @staticmethod
    def _folder_build(folder: Path) -> str | None:
        """The XDK build number from a folder named after it ("20353", "21256.3"), which stays unique per copy
        even when several XDKs share one cl.exe version resource. The XDK folder itself is checked after its
        parent, so pointing at either <build> or <build>/XDK gives the same answer."""
        for name in (folder.parent.name, folder.name):
            if name[:1].isdigit() and all(c.isdigit() or c == "." for c in name):
                return name
        return None

    def build(self, folder: Path) -> str | None:
        """The compiler version from cl.exe's PE version resource ("16.0.11886.0"), else c1.dll's, else the
        XDK build from the containing folder's name ("20353"). None when nothing can be read."""
        root = self.locate(folder) or folder
        for rel in (CL, C1):
            at = find(root, rel)
            version = file_version(at) if at else ""
            if version.startswith("16."):  # cl.exe's 16.00.x is the version a game's banner prints
                return version
        return self._folder_build(root)

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        """Expose cl.exe even when the copy has no headers (most of the extracted builds are compiler-only):
        the project build can add includes from the kit if they are there. Libs (XDK/lib/xbox) are found too but
        OwnCompiler has no field for them, so the project build reads them from the kit folder itself."""
        root = self.locate(folder)
        if root is None:
            return []
        cl = find(root, CL)
        if cl is None:
            return []
        # The id is the XDK build the folder is named after. cl.exe's version resource can't be it: XDK 20353,
        # 21119 and 21256.3 all say 16.0.11886.0, so they collapsed into one compiler (kits.compilers keeps one per
        # id) and neither the survey nor the compiler picker could tell them apart. The version-resource id they
        # were known by stays an alias (kits.resolve), so a project that recorded it keeps building.
        folder_build = self._folder_build(root)
        version = self.build(root) or "unknown"
        build = folder_build or version
        aliases = (f"{VERSION}-{version}",) if folder_build and version != folder_build else ()
        include = find(root, "include/xbox", dirs=True)
        includes = (include,) if include else ()
        # A full copy has XDK/lib/xbox too; OwnCompiler has no field for libraries, so the project build reads
        # them from `root` when it needs them rather than putting a platform-specific path in `flags`.
        name = f"XDK {build} PowerPC C/C++" + (f" ({version})" if version != build else "")
        return [OwnCompiler(
            f"{VERSION}-{build}", name, cl, includes,
            runner="wine", build=version, root=root, args=(), flags=(), aliases=aliases,
        )]


XDK360 = Xdk360()
