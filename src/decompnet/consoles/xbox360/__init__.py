"""Xbox 360 XEX2 programs, recognised so a game's identity and header facts can be reported.

Stage 1 sniffs an XEX2 file and describes it (``scratch.py`` reads the few header fields
it needs). Stage 2a adds the user-supplied xsdk kit (``kits/xsdk.py``: the free GCC 15
cross toolchain and SDK, a user copy rather than a pinned pack) alongside the user's real
Microsoft XDK (``kits/xdk360.py``: the PowerPC cl.exe retail games used, run under Wine)
and a small original demo (``projects/demo-xbox360``). Stage 3 creates a PROJECT-mode decomp project from a
``functions.jsonl`` map beside the game (no Ghidra needed). Stage 4a discovers functions
with Ghidra, through a disposable ELF64/PPC64 wrapper, when there is no map. For a
basic-compressed XEX2 the project judges against an expanded image written to
``orig/<version>.image.bin``, not the raw compressed file.
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
    # the manifest holds xdk360_compiler, the one global compiler selection (see file_compiler below)
    compiler_config = (".decomp.wtf.json",)
    kits = ("xsdk", "xdk360")  # the user's own xsdk toolchain and SDK, and optionally their real Microsoft XDK
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
        from . import scratch

        return scratch.create(root, game, compiler=compiler, log=log)

    def game_info(self, game: Path) -> dict:
        from . import scratch
        return scratch.game_info(game)

    def tool_args(self) -> list[str]:
        # The xsdk kit supplies the compiler and flags at build time (XSDK_CC / XSDK_FLAGS read by the
        # project template's configure.py). There is no pinned pack, so no fixed build args to pass here.
        return []

    def build_env(self) -> dict[str, str]:
        """Where wine and the app's prefix are (DECOMPNET_WINE / DECOMPNET_WINE_PREFIX), read by a project's
        tools/compile.py when the real XDK's cl.exe builds a unit under Wine. As on the PC and the original Xbox,
        this is read at build time, not baked into build.ninja, so an older configure.py is never given an argument
        it doesn't know and the env is picked up as soon as wine is installed."""
        from ... import wine

        return wine.build_env(always=True)

    def file_compiler(self, fp, source: str) -> str | None:
        """The registered XDK copy this project builds with (one of project_compilers), so forkengine.try_compiler
        can switch among the user's XDK copies and identify which one built the game.

        X360 has ONE global compiler for every source file, not PC's per-file map, so ``source`` is deliberately
        ignored: switching it reconfigures the whole project's build. With no recorded selection the answer is the
        same copy tools/configure.py defaults to (verified by that default's mirroring in scratch), which keeps
        ``own`` non-None and lets try_compiler proceed. Returns None only for a project with no manifest at all."""
        from . import scratch

        manifest = fp.manifest
        if manifest is None:
            return None
        return scratch.xdk360_compiler(manifest) or scratch.selected_xdk360_compiler()

    def set_file_compiler(self, fp, source: str, compiler: str) -> str:
        """Make the whole project build with ``compiler`` (an id from project_compilers), recording it in the
        manifest and regenerating build.ninja, which bakes the compiler into its `cc` rule. ``source`` is ignored:
        X360 has a single global compiler. Returns the manifest file for forkengine to commit. The switch is what
        try_compiler judges with (reusing its _attempt/_build_unit and restore loop); what is NOT verified here is
        that any real XDK copy reproduces a real game's bytes - the tests use synthetic kits with no Wine run."""
        from . import scratch

        manifest = fp.manifest or {}
        scratch.set_xdk360_compiler(fp.root, manifest.get("version"), compiler)
        return ".decomp.wtf.json"

    def on_start(self, net, slug: str, log=print) -> None:
        """A project imported by an older decompdecomp keeps the build tools it was made with, so fixes to them
        never reached it: its stale configure.py kept selecting the free xsdk GCC even when a real XDK was
        registered. Bring those tools up to the current template (scratch.write_tools) once, when the stamp in
        the manifest differs from scratch.tools_version(), and remember it so it is tried only when tools change."""
        from . import scratch

        if not net.fork_slug(slug):
            return
        fp = net.forks.get(slug)[1]
        manifest = fp.manifest or {}
        if manifest.get("created") != "from scratch" or manifest.get("tools") == scratch.tools_version():
            return
        changed = scratch.write_tools(fp.root)
        fp.update_manifest(tools=scratch.tools_version())
        if changed:
            log(f"updated this project's build tools to decompdecomp's current ones ({', '.join(changed)})")


XBOX360 = Xbox360()
