"""Compile one Xbox 360 unit with the user's xsdk GCC (PowerPC64, big-endian) or their real XDK's cl.exe.

Invoked by build.ninja, which passes the compiler, its runner ("wine" for the real XDK) and the flags from
configure.py. cl.exe is a Windows program: under wine it takes MSVC switches (/I, /c, /Fo), so XSDK_FLAGS must be
MSVC-style when the real XDK is chosen. No shell parses the paths or flags. Made by decompdecomp.
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

COMPILE_SECONDS = 300
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--runner", default="")  # "wine" for a real XDK's cl.exe, "" for a native compiler
    parser.add_argument("--include", required=True)
    parser.add_argument("--config", required=True)  # kept for parity with other connectors; unused here
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("flags", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    flags = args.flags[1:] if args.flags[:1] == ["--"] else args.flags
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    env = None
    if args.runner == "wine":
        # The same wine the app uses: $DECOMPNET_WINE, else the PATH, in the prefix $DECOMPNET_WINE_PREFIX names
        # (never ~/.wine). A real XDK copy still needs its headers and libraries, which this kit does not bundle.
        wine = os.environ.get("DECOMPNET_WINE") or shutil.which("wine") or "wine"
        command = [wine, args.compiler, *flags, f"/I{args.include}", "/I.", "/c", args.source,
                   f"/Fo{args.output}"]
        if prefix := os.environ.get("DECOMPNET_WINE_PREFIX"):
            env = {**os.environ, "WINEPREFIX": prefix}
    else:
        command = [args.compiler, *flags, f"-I{args.include}", "-I.", "-c", args.source, "-o", args.output]
    print(shlex.join(command))
    return subprocess.run(command, timeout=COMPILE_SECONDS, creationflags=NO_WINDOW, env=env).returncode


if __name__ == "__main__":
    sys.exit(main())
