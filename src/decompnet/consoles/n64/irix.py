"""Run pinned SGI compiler roots under their bundled Linux IRIX emulator.

These are explicit comparison profiles, not substitutes for the native IDO packs.
The early compiler emits ECOFF; its pinned converter is adapted to preserve big
endian code, symbols and relocations before the ordinary ELF linker judges it.
"""
from __future__ import annotations

import argparse
import importlib.util
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace


def convert_ecoff(converter: Path, source: Path, output: Path) -> None:
    """The pinned converter writes little-endian ELF by default; N64 needs BE throughout."""
    import sys

    spec = importlib.util.spec_from_file_location('_decomp_ecoff', converter)
    if spec is None or spec.loader is None:
        raise ValueError('The IDO 4.1 ECOFF converter is missing: reinstall the toolchain.')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolves annotations against its module
    try:
        spec.loader.exec_module(module)
        ecoff = module.EcoffFile.from_path(source)
        if ecoff.code_endian != module.Endian.BIG:
            raise ValueError('IDO 4.1 produced little-endian code; keep the N64 -EB setting.')
        # Change ELF serialization only. ECOFF reads already respect their own byte order.
        module.struct = SimpleNamespace(**{k: getattr(struct, k) for k in dir(struct) if k != 'pack'},
                                        pack=lambda fmt, *args: struct.pack('>' + fmt[1:] if fmt.startswith('<') else fmt, *args))
        module.ElfBuilder.ELFDATA2LSB = 2
        module.save_as_elf(ecoff, output)
    finally:
        sys.modules.pop(spec.name, None)


def compile_irix(root: Path, profile: str, source: Path, output: Path, include: Path, flags: list[str]) -> None:
    root = root.resolve()
    early = profile == 'ido-4.1'
    qemu = root / ('usr/bin/qemu-irix-4.0' if early else 'usr/bin/qemu-irix')
    executable = root / ('usr/bin/cc' if early else 'usr/lib/CC' if profile == 'ido-5.3-cxx' else 'usr/lib/driver')
    for tool in (qemu, executable):
        if not tool.is_file():
            raise ValueError(f'Missing {tool.name}: reinstall the SGI comparison toolchain.')
    obj = output.with_suffix('.ecoff') if early else output
    argv = [str(qemu), '-silent', '-L', str(root), str(executable), '-c', '-EB',
            '-I' + str(include.resolve()), '-I' + str(root / 'usr/include'),
            *flags, str(source), '-o', str(obj)]
    subprocess.run(argv, check=True, timeout=60)
    if early:
        convert_ecoff(root / 'usr/bin/ecoff_tool.py', obj, output)
    if not output.is_file() or output.read_bytes()[:6] != b'\x7fELF\x01\x02':
        raise ValueError('The SGI compiler did not produce a big-endian ELF32 object; keep the 32-bit N64 ABI.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--profile', required=True)
    for name in ('source', 'output', 'include'):
        parser.add_argument(name, type=Path)
    parser.add_argument('flags', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        compile_irix(args.root, args.profile, args.source, args.output, args.include, args.flags)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'SGI compilation failed: {error}\n')


if __name__ == '__main__':
    main()
