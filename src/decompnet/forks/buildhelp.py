"""Explain confirmed public-build prerequisite failures without changing compiler choices or project files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..hostenv import NO_WINDOW


def explain(root: Path, executable: str, output: str) -> str:
    if Path(executable).name not in ('make', 'gmake'):
        return ''
    try:
        text = (root / 'Makefile').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''
    hints = []
    # Old Make silently treats != as part of a variable name, skipping mkdir/tool-building side effects.
    if re.search(r'^\s*[\w.-]+\s*!=', text, re.M):
        try:
            result = subprocess.run([executable, '--version'], capture_output=True, text=True,
                                    errors='replace', timeout=5, creationflags=NO_WINDOW)
            version = re.search(r'GNU Make (\d+)\.(\d+)', result.stdout)
            if version and tuple(map(int, version.groups())) < (4, 0):
                hints.append(f"This Makefile uses shell assignments (!=), which require GNU Make 4.0 or newer. "
                             f"The selected build tool is GNU Make {version[1]}.{version[2]}; it skips these "
                             "assignments, so build directories and tools may not be created. Install a current "
                             "GNU Make and configure this project's build to use it, including recursive builds.")
        except (OSError, subprocess.TimeoutExpired):
            pass
    missing = re.findall(r'(?:^|\n)(?:make(?:\[\d+\])?: )?([\w.+-]+-cpp): (?:No such file or directory|Command not found)', output)
    for tool in dict.fromkeys(missing):
        hints.append(f"Missing cross preprocessor: {tool}. It is part of the project's cross-GCC toolchain, "
                     "not binutils. Install/configure the cross toolchain required by this project's build "
                     "instructions. decompdecomp has not substituted another preprocessor or compiler.")
    # Report literal missing compiler paths, but never guess a compiler version from an unversioned directory.
    for value in re.findall(r'^\s*(?:CC|IDO)\s*[:?]?=\s*([^\s#$]+)\s*$', text, re.M):
        if '/' in value and not Path(value).is_absolute() and not (root / value).is_file():
            hints.append(f"The Makefile also declares a missing compiler: {value}. Follow the project's setup "
                         "instructions for its exact compiler and version; an unversioned path does not identify "
                         "which compiler release will match.")
    return '\n\n'.join(dict.fromkeys(hints))
