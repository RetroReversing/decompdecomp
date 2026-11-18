"""Compile one Xbox 360 unit with the user's xsdk GCC (PowerPC64, big-endian).

Invoked by build.ninja, which passes the compiler from XSDK_CC and the flags from XSDK_FLAGS.
No shell parses the paths or flags. Made by decompdecomp.
"""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

COMPILE_SECONDS = 300
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--include", required=True)
    parser.add_argument("--config", required=True)  # kept for parity with other connectors; unused here
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("flags", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    flags = args.flags[1:] if args.flags[:1] == ["--"] else args.flags
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    command = [args.compiler, *flags, f"-I{args.include}", "-I.", "-c", args.source, "-o", args.output]
    print(shlex.join(command))
    return subprocess.run(command, timeout=COMPILE_SECONDS, creationflags=NO_WINDOW).returncode


if __name__ == "__main__":
    sys.exit(main())
