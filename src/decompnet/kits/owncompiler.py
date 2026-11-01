"""A compiler of the user's own, for the consoles decompdecomp splits and links itself (ROM mode: N64, PS1, PS2, PSP,
GBA): another build of a compiler one of the console's pinned packs already runs (an EE-GCC, a CodeWarrior PS2, an
SNC, a PSP GCC, a KMC GCC, an IDO, an agbcc), which a game was built with and no pack has. A copy is the folder that
compiler is in, laid out as the pack's is (its program at the same place: `bin/ee-gcc`, `mwccps2.exe`, `psp/bin/gcc`),
and it runs exactly as that pack runs (OwnCompiler.like: the pack's command, wibo, the PS1's maspsx and assembler...),
with the user's folder in the pack's place. Nothing is copied.

Which pack a copy runs like: the packs whose program the folder has; of those sharing one program, the ones whose
version ("2.96", "3.0.1") is written in its program or its cc1, else the first. A console lists the copies that run
like one of its own packs (Console.kit_compilers). The other project-mode consoles' compilers (CodeWarrior for
GameCube, Wii and DS) aren't here: their builds pick compilers from the community's archive by version, which holds
every known one (ROADMAP.md, "A compiler of your own").
"""

from __future__ import annotations

import functools
import hashlib
import re
import tomllib
from importlib import resources
from pathlib import Path

from . import Kit, OwnCompiler, file_version, find

# a pack's program in its command: "{tc}/bin/ee-gcc", or "$tc/gcc" inside a shell script; not its headers' folder
_PROGRAM = re.compile(r"(?:\{tc\}|\$tc)/([\w.+-]+(?:/[\w.+-]+)*)")
_VERSION = re.compile(r"\d+(?:\.\d+)+")


@functools.cache
def packs() -> dict[str, tuple[str, str, str, tuple[str, ...]]]:
    """Every pinned compiler a copy can run like: its id -> (its program's path in its folder, its name, how it runs:
    packs alike in that, the builds of one CodeWarrior, are one way to run a copy; its flags). Read from
    toolchains.toml itself: the registry asks the kits for their facts as it's made."""
    pinned = tomllib.loads(resources.files("decompnet").joinpath("toolchains.toml").read_text(encoding="utf-8"))
    out = {}
    for tid, spec in pinned.items():
        if spec.get("kind", "compiler") != "compiler":
            continue
        program = next((m for m in _PROGRAM.findall(" ".join([*spec.get("cc", []), *spec.get("cmd", [])]))
                        if m != "include"), None)
        if program:
            out[tid] = (program, spec.get("name", tid), repr((spec.get("cc"), spec.get("cmd"), spec.get("requires"))),
                        tuple(spec.get("flags", ())))
    return out


class OwnCompilers(Kit):
    id = "own-compiler"
    name = "A compiler of your own"
    platform = "N64, PS1, PS2, PSP, GBA"
    description = ("Another build of a compiler decompdecomp has for these consoles (an EE-GCC, a CodeWarrior PS2, an "
                   "SNC, a PSP GCC...), for a game built with one no pack has. Give the folder it's in, laid out as "
                   "the pack's is; it runs the same way.")

    @property
    def files(self) -> tuple[str, ...]:  # (what Setup says the folder should hold: one of these)
        return tuple(dict.fromkeys(program for program, *_ in packs().values()))

    def locate(self, folder: Path) -> Path | None:
        try:
            return folder.resolve() if self.compilers(folder) else None
        except OSError:
            return None

    def build(self, folder: Path) -> str | None:
        return ", ".join(c.build for c in self.compilers(folder) if c.build) or None

    def verify(self, folder: Path) -> str:
        found, names = self.compilers(folder), packs()
        return "runs like " + ", ".join(names[c.like][1] for c in found) if found else ""

    def compilers(self, folder: Path) -> list[OwnCompiler]:
        by_program: dict[Path, list[tuple[str, str, str]]] = {}
        for tid, (program, name, runs, _) in packs().items():
            if (exe := find(folder, program)) is not None:
                by_program.setdefault(exe, []).append((tid, name, runs))
        out = []
        for exe, kinds in by_program.items():
            versions = {tid: v.group() for tid, _, _ in kinds if (v := _VERSION.search(tid))}
            stat = exe.stat()
            digest, seen = _read(str(exe), stat.st_mtime_ns, stat.st_size, str(folder), tuple(versions.values()))
            built = file_version(exe) if exe.suffix.lower() == ".exe" else ""
            alike = {runs: (tid, name) for tid, name, runs in reversed([k for k in kinds if versions.get(k[0]) in seen]
                                                                       or kinds[:1])}  # (the first of those alike)
            for tid, name in alike.values():  # (its id starts with the pack's: what a console's flags go by, "mwcps2")
                out.append(OwnCompiler(f"{tid}-own-{digest}", f"{name}, your copy in {folder.name}", exe, (),
                                       runner="", build=built, root=folder.resolve(), flags=packs()[tid][3], like=tid))
        return out


@functools.lru_cache(maxsize=64)
def _read(exe: str, mtime_ns: int, size: int, folder: str, versions: tuple[str, ...]) -> tuple[str, frozenset[str]]:
    """A program's hash (what its compiler id is made of) and which of `versions` its bytes or its cc1's name. Cached
    by its time and size: a console's compiler list asks often."""
    data = Path(exe).read_bytes()
    near = [*Path(exe).parent.glob("cc1*"), *Path(folder).glob("lib/gcc-lib/*/*/cc1*"), *Path(folder).glob("cc1*")]
    text = data + b"".join(p.read_bytes() for p in sorted(set(near))[:4] if p.is_file())
    return hashlib.sha256(data).hexdigest()[:8], frozenset(v for v in versions if v.encode() in text)


OWN_COMPILERS = OwnCompilers()
