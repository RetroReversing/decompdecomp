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

import json
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
    # Autopilot's drafts come from Ghidra instead (drafts.py): forks/ghidra.py decompiles the delinked
    # ELF64 object objdiff judges against, which is the only machine decompiler that reads PPC64 here.
    own_drafts = True
    ram = (0x80000000, 0x90000000)  # Xenon's 1 GB virtual address space
    demo = "demo-xbox360"  # a small original C demo, built into a XEX2 by its build.py
    demo_tools = ("ghidra", "objdiff-cli")  # what importing and judging that demo needs

    def sniff(self, head: bytes, file) -> bool:
        """A XEX2 program -- or the disc image one sits in: an XDVDFS volume with a ``default.xex`` at its root.

        A disc can't be recognised from its first bytes (the volume descriptor is at sector 32 of the game
        partition, after a video partition on XGD2/XGD3 discs), and that video partition's ISO 9660 filesystem is
        what made a disc image look like a PlayStation game to the sniffs asked before this one. The file itself is
        therefore asked, and only for its own filesystem's boot executable, so an original Xbox disc (default.xbe)
        still belongs to the Xbox.
        """
        from .scratch import is_xex2

        if is_xex2(head):
            return True
        if not isinstance(file, Path):
            return False
        from . import iso

        try:
            with iso.Disc(file) as disc:
                disc.find("default.xex")
        except (iso.IsoError, OSError):
            return False
        return True

    def upload_name(self, data: bytes) -> str:
        from .scratch import MAGIC

        return "game.xex" if bytes(data[:4]) == MAGIC else "game.iso"

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
        """The registered XDK copy ``source`` builds with: its own from the project's compiler map, else the
        project's default (which is also what tools/configure.py falls back to).

        Forkengine's try_compiler and the Settings compiler map both ask per file: a file the map names another copy
        for is built with it, and every other file keeps the project's one. Returns None only for a project with no
        manifest at all."""
        from . import scratch

        if fp.manifest is None:
            return None
        return scratch.xdk360_file_compiler(fp.manifest, source)

    def set_file_compiler(self, fp, source: str, compiler: str) -> str:
        """Build ``source`` with ``compiler`` (an id from project_compilers) and regenerate build.ninja, which now
        names one rule per compiler the project builds with. Naming the project's own default drops the file's entry,
        so the map holds only real overrides. Returns the manifest file for forkengine to commit.

        Forkengine's verification wraps this: it records which functions matched before, rebuilds, and keeps the
        change only when they still match."""
        from . import scratch

        manifest = fp.manifest or {}
        scratch.set_xdk360_file_compiler(fp.root, manifest.get("version"), source, compiler)
        return ".decomp.wtf.json"

    def file_ranges(self, fp) -> dict[str, list[tuple[int, int]]]:
        """Where each source file's code lives in the game, by the unit's functions in ``config/<version>/functions.json``.

        The Settings panel's "only for these address ranges" and the compiler map's suggestions need this, and an
        Xbox 360 project has no ``vram`` on its functions: the addresses only exist in the analysis each unit was
        cut from, so they are read from there rather than from the database.
        """
        version = (fp.manifest or {}).get("version")
        try:
            config = json.loads((fp.root / "config" / str(version) / "functions.json").read_text(encoding="utf-8"))
            objdiff = json.loads((fp.root / "objdiff.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        sources = {(unit.get("name") or ""): ((unit.get("metadata") or {}).get("source_path") or "")
                   for unit in objdiff.get("units") or []}
        ranges: dict[str, list[tuple[int, int]]] = {}
        for unit, entries in (config.get("units") or {}).items():
            source = sources.get(unit) or ""
            spans = [(entry["addr"], entry["addr"] + max(int(entry.get("size") or 0), 1))
                     for entry in entries or [] if isinstance(entry.get("addr"), int)]
            if source and spans:
                ranges[source] = [(min(start for start, _ in spans), max(end for _, end in spans))]
        return ranges

    def prepare_drafts(self, fp) -> str | None:
        """Ghidra's drafts need its types declared: write ``include/ghidra.h`` when the project lacks it."""
        from .drafts import GHIDRA_H, GHIDRA_HEADER

        path = fp.root / "include" / GHIDRA_HEADER
        if path.exists():
            return None
        path.write_text(GHIDRA_H, encoding="utf-8")
        return path.relative_to(fp.root).as_posix()

    def draft_symbols(self, fp) -> dict[str, str]:
        """Every function the project names, by that name, so a draft's callee keeps it instead of Ghidra's
        ``func_0x<offset>``, which names nothing outside the delinked object it came from."""
        version = (fp.manifest or {}).get("version")
        try:
            info = json.loads((fp.root / "config" / str(version) / "functions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        names = {fn.get("name") for units in (info.get("units") or {}).values() for fn in units}
        return {name: name for name in names if name}

    def compilable_draft(self, fp, symbol: str, draft: str, known: str = "") -> tuple[str, str] | None:
        """Ghidra's decompilation of this function as C the PPC compiler accepts (drafts.py).

        No exact drafts yet: PowerPC leaf shapes would be a console-specific reader of the instruction
        forms (as ``consoles/pc/drafts.py`` does for x86), so autopilot's only draft here is Ghidra's.
        """
        from . import drafts

        return drafts.compilable(draft, symbol, self.draft_symbols(fp), known)

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
