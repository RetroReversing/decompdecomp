"""Xbox 360 XEX2 programs, recognised so a game's identity and header facts can be reported.

Stage 1 sniffs an XEX2 file and describes it (``scratch.py`` reads the few header fields
it needs). Stage 2a adds the user-supplied xsdk kit (``kits/xsdk.py``: the free GCC 15
cross toolchain and SDK, a user copy rather than a pinned pack) and a small original demo
(``projects/demo-xbox360``). Project creation and Ghidra integration are still later work.
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
    # PLACEHOLDER: xsdk is a user copy (a kit, not a downloadable pin), so this stays as
    # the default id for now. Conformance needs a non-empty compiler list; the real GCC
    # appears beside it from the user's kit via kit_compilers() once it is set up.
    compilers = (Compiler("xbox360-xsdk-placeholder", "Xbox 360 XDK (placeholder)",
                          "PLACEHOLDER - the xsdk kit supplies the real compiler"),)
    packs = ()
    kits = ("xsdk",)  # the user's own xsdk toolchain and SDK (kits/xsdk.py)
    guides = ("ppc", "cpp")
    compiler_guide = ""  # no new guide: xsdk's own README documents the toolchain
    m2c = ""  # m2c has no PowerPC64 target (as with the PS3)
    ram = (0x80000000, 0x90000000)  # Xenon's 1 GB virtual address space
    demo = "demo-xbox360"  # a small original C demo, built into a XEX2 by its build.py
    demo_tools = ("ghidra", "objdiff-cli")  # what importing and judging that demo needs

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
        # The xsdk kit supplies a compiler once set up; no pinned pack, so no fixed build args.
        return []


XBOX360 = Xbox360()
