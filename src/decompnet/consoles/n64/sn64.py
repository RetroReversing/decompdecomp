"""Original SN64 compiler + assembler, with a pinned PsyQ-to-ELF converter.

Invoked as a standalone script inside the normal compiler sandbox. No game data,
tool installation or executable lookup belongs here; every tool path is explicit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


def assembler_input(text: str) -> str:
    # SN's assembler predates GNU's ELF metadata directives. Function extent is
    # recovered by the ELF reader from adjacent symbols after conversion.
    text = re.sub(r"^\s*\.(?:version|type|size)\b[^\n]*", "", text, flags=re.M)
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def compile_source(cc: Path, cpp: Path, assembler: Path, converter: Path, runner: Path,
                   source: Path, output: Path, include: Path, flags: list[str]) -> None:
    for tool in (cc, cpp, assembler, converter, runner):
        if not tool.is_file():
            raise ValueError(f'Missing {tool.name}: reinstall the SN64 toolchain and its converter in Compilers & tools.')
    def run(argv):
        subprocess.run([str(a) for a in argv], check=True, timeout=30)

    preprocessed = output.with_suffix('.sn.i')
    assembly = output.with_suffix('.sn.s')
    obj = output.with_suffix('.sn.obj')
    defines = [f for f in flags if f.startswith(('-D', '-U', '-I'))]
    compiler_flags = [f for f in flags if f not in defines]
    run([runner, cpp, '-P', '-undef', '-nostdinc', '-D_LANGUAGE_C', '-D__GNUC__=2',
         '-I' + str(include), *defines, source, '-o', preprocessed])
    run([runner, cc, '-quiet', *compiler_flags, preprocessed, '-o', assembly])
    assembly.write_bytes(assembler_input(assembly.read_text(encoding='utf-8', errors='replace')).encode('utf-8'))
    run([runner, assembler, '-mips3', '-I', include, '-o', obj, assembly])
    run([converter, obj, '-o', output, '-b', '-n'])
    if not output.is_file() or output.read_bytes()[:6] != b'\x7fELF\x01\x02':
        raise ValueError('The SN64 converter did not produce a big-endian ELF32 object.')


def main():
    parser = argparse.ArgumentParser()
    for option in ('cc', 'cpp', 'assembler', 'converter', 'runner'):
        parser.add_argument('--' + option, type=Path, required=True)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('include', type=Path)
    parser.add_argument('flags', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        compile_source(args.cc, args.cpp, args.assembler, args.converter, args.runner,
                       args.source, args.output, args.include, args.flags)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'SN64 compilation failed: {error}\n')


if __name__ == '__main__':
    main()
