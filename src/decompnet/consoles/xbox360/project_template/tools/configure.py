#!/usr/bin/env python3
"""Write build.ninja and objdiff.json for an Xbox 360 decompilation.

    XSDK_CC=powerpc-xsdk-elf-gcc python tools/configure.py VERSION
    DECOMP_X360_COMPILER=xdk360-21256.3 python tools/configure.py VERSION

The compiler is chosen, in order: by --compiler or DECOMP_X360_COMPILER (the version id of one specific registered
xdk360 XDK copy, e.g. xdk360-21256.3, whose cl.exe runs with that copy's runner, "wine"); by the project manifest's
``xdk360_compiler`` key (what forkengine's try_compiler records when a version matches); by XSDK_CC; else by the first
registered xdk360 XDK (the real Microsoft cl.exe), else the xsdk kit registered in Setup, else
powerpc-xsdk-elf-gcc on PATH. No environment variable and no selection keeps the old behaviour: the real XDK is
preferred over xsdk because a game built with it needs that compiler. Nothing here comes from a pinned pack. For the
real XDK the generated rule runs cl.exe through Wine and uses MSVC switches, so XSDK_FLAGS must then be MSVC-style
too (see the template README). The build cuts every function out of the user's own XEX2 copy into ELF64 PPC64
objects (tools/delink.py), one per source file, which objdiff compares with the compiled candidate. Made by
decompdecomp.
"""

import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # the project root (this file is tools/configure.py inside it)
MANIFEST = ROOT / ".decomp.wtf.json"
COMPILER_ENV = "DECOMP_X360_COMPILER"  # an explicit registered-copy id, set by try_compiler's compiler switch
RUNNER_ENV = "DECOMP_X360_RUNNER"      # override what wraps an XSDK_CC that ends in .exe (default: wine)


def _registered(cid: str):
    """The registered xdk360 copy whose version is ``cid``, or None. Kept separate so callers can resolve the same
    id the console exposes (OwnCompiler.version, e.g. xdk360-21256.3) to the exact cl.exe it points at."""
    try:  # the project runs under the app's Python, where decompnet (and the registered kits) is available
        from decompnet import kits
        return next((own for own in kits.compilers(["xdk360"]) if own.version == cid), None)
    except Exception:  # no decompnet here, or no kit set up: the caller falls back
        return None


def find_compiler(compiler_id: str | None = None) -> tuple[str, str]:
    """(compiler, runner): which cl.exe (or cross gcc) this project builds with, and what runs it.

    An explicit id (argument or DECOMP_X360_COMPILER) and the manifest's persisted selection both name ONE registered
    xdk360 copy: it is resolved to that copy's cl.exe with the runner its kit recorded. That is what lets a game
    identify its XDK build instead of compiling with whichever copy registered first. XSDK_CC is still honoured; an
    .exe given there defaults to Wine (DECOMP_X360_RUNNER overrides that), which fixes the old empty-runner case
    where a cl.exe set through XSDK_CC was executed directly.
    """
    chosen = compiler_id or os.environ.get(COMPILER_ENV)
    if not chosen and MANIFEST.is_file():
        try:
            chosen = json.loads(MANIFEST.read_text(encoding="utf-8")).get("xdk360_compiler")
        except (OSError, ValueError):
            chosen = None
    if chosen:
        own = _registered(chosen)
        if own is not None and own.exe.is_file():
            return str(own.exe), own.runner
        sys.stderr.write(f"warning: registered XDK {chosen!r} not found; falling back to the first available compiler\n")
    given = os.environ.get("XSDK_CC")
    if given:
        runner = os.environ.get(RUNNER_ENV)
        if runner is None:  # present-but-empty means "native": only default when the variable was not given at all
            runner = "wine" if given.lower().endswith(".exe") else ""
        return given, runner
    try:
        from decompnet import kits
        for kid in ("xdk360", "xsdk"):
            for own in kits.compilers([kid]):
                if own.exe.is_file():
                    return str(own.exe), own.runner
    except Exception:  # no decompnet here, or no kit set up: fall back to PATH and warn below if that fails too
        pass
    return "powerpc-xsdk-elf-gcc", ""


def q(argv: list[str]) -> str:
    return shlex.join(argv)


def main() -> None:
    parser = argparse.ArgumentParser(description="Writes build.ninja and objdiff.json")
    parser.add_argument("version", help="game version (a directory in config/)")
    parser.add_argument("--compiler", default="", help="version id of one registered xdk360 copy (e.g. "
                        "xdk360-21256.3); overrides DECOMP_X360_COMPILER and the project's recorded selection")
    args = parser.parse_args()
    os.chdir(ROOT)
    cc, runner = find_compiler(args.compiler or None)

    config = Path("config") / args.version / "functions.json"
    build = Path("build") / args.version
    flags = os.environ.get(
        "XSDK_FLAGS",
        "-mcpu=cell -fno-pic -mcall-xsdk -ffreestanding -fno-builtin -fno-exceptions -fno-rtti",
    ).split()

    if runner == "wine":
        # cl.exe is a Windows program: the compiler path itself exists, but it is wine that must be on this machine.
        if shutil.which(os.environ.get("DECOMPNET_WINE") or "wine") is None:
            sys.stderr.write("warning: wine not found; the real XDK's cl.exe needs it (see the template README)\n")
    elif shutil.which(cc) is None:
        sys.stderr.write(f"warning: {cc!r} not found; install your xsdk kit in Setup or set XSDK_CC before building\n")
    units = json.loads(config.read_text(encoding="utf-8"))["units"]
    compile_cmd = q([
        sys.executable, "tools/compile.py", "--compiler", cc, "--runner", runner, "--include", "include",
        "--config", str(config), "--output", "$out", "--source", "$in", "--", *flags,
    ])
    # delink.py takes the whole functions.json and an output directory, writing one object per unit; a single
    # ninja edge with all target objects as outputs runs it once. restat keeps unchanged objects' timestamps.
    delink_cmd = q([sys.executable, "tools/delink.py", str(config), str(build / "target")])
    objects = [str(build / "src" / "auto" / f"{u}.o") for u in units]
    targets = [str(build / "target" / f"{u}.o") for u in units]
    lines = [
        "# generated by tools/configure.py; edit tools/compile.py or XSDK_FLAGS instead",
        f"rule delink\n  command = {delink_cmd}\n  description = DELINK {config}\n  restat = 1",
        f"rule cc\n  command = {compile_cmd}\n  description = CC $in",
    ]
    lines.append(f"build {' '.join(targets)}: delink {config} tools/delink.py")
    for unit, obj in zip(units, objects):
        lines.append(f"build {obj}: cc src/auto/{unit}.c | {config}")
    # Delinking the target objects reads only this project's image and functions.json (stdlib), while compiling
    # the candidate objects needs the user's xsdk kit. Split them so an import can build just the targets, and
    # leave the full build (targets + candidates) as the default for when the kit is installed.
    lines.append("build prepare-targets: phony " + " ".join(targets))
    lines.append("build prepare: phony " + " ".join(["prepare-targets", *objects]))
    lines.append("default prepare")
    Path("build.ninja").write_text("\n".join(lines) + "\n", encoding="utf-8")

    objdiff = {
        "$schema": "https://raw.githubusercontent.com/encounter/objdiff/main/config.schema.json",
        "custom_make": "ninja", "custom_args": [], "build_target": False, "build_base": True,
        "watch_patterns": ["*.c", "*.h"],
        "options": {"functionRelocDiffs": "all", "combineTextSections": True},
        "units": [{"name": u,
                   "target_path": str(build / "target" / f"{u}.o"),
                   "base_path": str(build / "src" / "auto" / f"{u}.o"),
                   "metadata": {"source_path": f"src/auto/{u}.c", "complete": False}}
                  for u in units],
    }
    Path("objdiff.json").write_text(json.dumps(objdiff, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
