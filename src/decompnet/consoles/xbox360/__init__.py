"""Xbox 360 XEX2 programs, recognised so a game's identity and header facts can be reported.

Stage 1 only sniffs an XEX2 file and describes it (``scratch.py`` reads the few header
fields it needs). Project creation, a pinned Xbox 360 compiler, a demo game and Ghidra
integration are deliberately left for later stages and are not implemented here.
"""

from __future__ import annotations

from pathlib import Path

from .. import BETA, PROJECT, Compiler, Console


class Xbox360(Console):
    id = "xbox360"
    name = "Xbox 360"
    short = "360"
    status = BETA
    arch = "ppc64"  # the Xenon is a 64-bit big-endian PowerPC; PS3 is the ppc64 precedent
    big_endian = True
    mode = PROJECT
    layout = "Ghidra + objdiff"
    # PLACEHOLDER: replaced by the pinned Xbox SDK (xsdk) compiler in stage 2. It is
    # kept non-empty because the shared conformance test expects a default compiler.
    compilers = (Compiler("xbox360-xsdk-placeholder", "Xbox 360 XDK (placeholder)",
                          "PLACEHOLDER - replaced by the xsdk pin in stage 2"),)
    packs = ()
    guides = ("ppc", "cpp")
    compiler_guide = ""  # no new guide in stage 1
    m2c = ""  # m2c has no PowerPC64 target (as with the PS3)
    ram = (0x80000000, 0x90000000)  # Xenon's 1 GB virtual address space
    demo = ""  # no homemade demo game until a later stage

    def sniff(self, head: bytes, file) -> bool:
        """A structurally plausible XEX2 (magic + sane header offsets)."""
        from .scratch import is_xex2
        return is_xex2(head)

    def upload_name(self, data: bytes) -> str:
        return "game.xex"

    def create_project(self, root: Path, game: Path, *, compiler: str | None = None, log=print) -> dict:
        raise NotImplementedError(
            "Xbox 360 project creation is not implemented yet: this stage only sniffs "
            "XEX2 files and reports game info. Project creation, the compiler pin and "
            "Ghidra integration are planned for a later stage."
        )

    def game_info(self, game: Path) -> dict:
        from . import scratch
        return scratch.game_info(game)

    def tool_args(self) -> list[str]:
        # No pinned compiler to point a project build at until the stage 2 xsdk pin.
        return []


XBOX360 = Xbox360()
