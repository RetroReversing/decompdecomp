"""Automatic Xbox 360 XDK compiler survey: find which registered XDK copy builds a project best.

N64 ROM projects pick their compiler at import: ``engine.importing.test_compilers`` drafts small
functions with m2c, builds them with every installed compiler, and makes the one matching most the
project's default. Project-mode X360 has no m2c (``Xbox360.m2c == ""``) and judges with objdiff, so the
same survey is done here against the C the project already has: a saved best attempt (or a saved
machine draft/seed) for a small deterministic sample of functions is built with each registered
``xdk360`` copy, one copy at a time, and each copy is scored by how many of those functions match
byte for byte (fork mode's 100% objdiff verdict). The best copy becomes the project's default.

Two things keep that honest. Copies that ship the identical ``cl.exe`` are one compiler, so they are
built once and share the score (``compiler_groups``): a collection of 30 setup media held 13 distinct
compilers. And every PE file names when it was built, so the survey reads the game's own link date
from the PE inside its XEX2 and each copy's compiler date from its ``cl.exe``: an XDK built after the
game was linked cannot have made it, and on a sample no compiler discriminates, the newest XDK that
already existed is the one the title's own date points at (the same narrowing ROM mode's compiler
ranges do for N64 SDK builds).

What is and isn't claimed: this is a *survey* over the project's existing C, not a proof of retail
correctness. A function with no saved C can't be judged, a seed may not be compilable C, and a
handful of getters that any compiler builds identically give no evidence. A copy that wins here is
the best of the candidates *on this sample*; it is set as the default so later builds use it, and
the ranked result is stored in ``compiler_tests`` like ROM mode's. The project is always left with
its original compiler when the survey fails part way through.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path

from ..network import NetworkError

KID = "xdk360"


def registered_compilers():
    """Every registered xdk360 copy (``OwnCompiler``: version, exe, runner wine, one per XDK build)."""
    from .. import kits

    return kits.compilers([KID])


COMPILER_FILES = ("cl.exe", "c1.dll", "c1xx.dll", "c2.dll")  # the driver and the C front/back ends


def _compiler_digest(exe: Path) -> str:
    """What actually decides the code a copy produces: its driver together with its C front and back ends.

    ``cl.exe`` is a thin driver -- ``c1xx.dll`` and ``c2.dll`` are the compiler -- so keying on the driver alone
    would call two copies identical that still build differently. A file the copy doesn't have contributes a
    stable placeholder rather than making the digest fail.
    """
    digest = hashlib.sha1()
    for name in COMPILER_FILES:
        try:
            digest.update(hashlib.sha1((exe.parent / name).read_bytes()).digest())
        except OSError:
            digest.update(f"missing:{name}".encode())
    return digest.hexdigest()


def compiler_groups(compilers) -> list[list]:
    """Copies that ship one and the same compiler, grouped; one member stands for the group.

    A downloaded XDK collection has many setup media that carry the identical compiler: on one machine 30
    registered copies were 13 distinct builds. Identical bytes are identical code, so testing each distinct
    compiler once and giving its score to every copy that shares it is both faster and more honest than
    recompiling the same compiler seventeen times and calling the repeats independent evidence.
    """
    groups: dict[str, list] = {}
    for own in compilers:
        exe = getattr(own, "exe", None)
        try:
            digest = _compiler_digest(Path(exe)) if exe else own.version
        except OSError:
            digest = own.version  # an unreadable copy stands alone rather than joining another
        groups.setdefault(digest, []).append(own)
    return list(groups.values())


def build_time(own) -> float | None:
    """When this copy's own compiler was built (``cl.exe``'s PE stamp), which bounds the games it can have made."""
    from ..consoles.xbox360 import scratch

    exe = getattr(own, "exe", None)
    return scratch.pe_timestamp(exe) if exe else None


def game_build_time(net, slug: str) -> float | None:
    """When the game itself was linked, from the PE inside its XEX2 (None when its copy isn't on disk)."""
    from ..consoles.xbox360 import scratch

    fork = _fork(net, slug)
    return scratch.game_build_time(fork.root, fork.manifest or {})


def _day(stamp: float | None) -> str | None:
    """A PE stamp as the day it names, for the log and the saved ranking (stamps are UTC seconds)."""
    return datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%d") if stamp else None


def _fork(net, slug: str):
    """The fork project behind an X360 project (its build is reconfigured by the compiler switch)."""
    return net.forks.get(slug)[1]


def _version(net, slug: str) -> str | None:
    """The game version configure.py needs when it regenerates build.ninja."""
    return (_fork(net, slug).manifest or {}).get("version")


def original_compiler(net, slug: str) -> str:
    """The copy the project builds with before the survey: its recorded choice, else configure.py's default."""
    from ..consoles.xbox360 import scratch

    manifest = _fork(net, slug).manifest or {}
    return scratch.xdk360_compiler(manifest) or scratch.selected_xdk360_compiler()


def sample_functions(net, slug: str, limit: int) -> list[dict]:
    """A small deterministic sample that can actually discriminate between compilers.

    Matched functions first: their C is proven to reproduce the game, so a compiler that no longer
    matches them is a real signal. Then the closest near-misses (lowest ``best_score``), then anything
    with saved C or a seed. Sorting only by address used to pick functions with no viable source, where
    every compiler scored 0 and the ranking said nothing.

    ``vram`` is null on some project-mode imports, so those sort last and ``id`` breaks ties.
    """
    proj = net._project(slug)
    rows = net.project_store(proj["id"]).all(
        "SELECT id, name, vram, status, best_score, best_source, seed, matched_source FROM functions "
        "WHERE project_id=? AND ("
        "(matched_source IS NOT NULL AND matched_source != '') "
        "OR (best_source IS NOT NULL AND best_source != '') "
        "OR (seed IS NOT NULL AND seed != '')) "
        "ORDER BY (status != 'matched'), "       # matched first: proven C discriminates compilers
        "CASE WHEN best_score IS NULL THEN 1 ELSE 0 END, best_score, "  # then the closest near-misses
        "(vram IS NULL), vram, id LIMIT ?",
        proj["id"],
        limit,
    )
    return [dict(row) for row in rows]


def _set_compiler(net, slug: str, version: str | None, compiler: str) -> None:
    """Point build.ninja at ``compiler`` without recording it: the survey tries copies, it doesn't choose one.

    ``record=True`` is only for the winner at the end, so a job the user cancels half way through leaves the
    project's own compiler recorded exactly as it was."""
    from ..consoles.xbox360 import scratch

    scratch.set_xdk360_compiler(_fork(net, slug).root, version, compiler, record=False)


def _choose_compiler(net, slug: str, version: str | None, compiler: str) -> None:
    """Record ``compiler`` as the project's XDK choice and regenerate build.ninja for it."""
    from ..consoles.xbox360 import scratch

    scratch.set_xdk360_compiler(_fork(net, slug).root, version, compiler)


def judge(net, slug: str, fn: dict, log: Callable[[str], None]) -> float | None:
    """How close this function's saved C gets to the game with the currently configured compiler.

    A diagnostic fork attempt judges with the build as it stands (build.ninja was regenerated by
    ``_set_compiler``), keeps nothing, and reads only the verdict: 0.0 is byte-for-byte identical, higher is
    further off, None means the source can't be built or found at all. Returning the distance rather than a
    yes/no is what lets the survey tell compilers apart on a sample where none of them is exact: one copy
    that brings a whole sample closer is evidence, and every copy tying at "no matches" is not.
    """
    source = (fn.get("matched_source") or fn.get("best_source") or fn.get("seed") or "").strip()
    if not source:
        return None
    current = net._function(fn["name"], slug)
    if current is None:
        return None
    try:
        agent = net.local_agent("you")[0]
        result = net.forks._attempt(agent, slug, current["name"], source, keep=False, diagnostic=True)
    except Exception as error:  # a Wine build can fail for many reasons; that copy just scores lower
        log(f"{fn['name']}: could not judge with the current compiler: {error}")
        return None
    if result.get("matched"):
        return 0.0
    score = result.get("score")
    return float(score) if score is not None else None


def record(net, slug: str, funcs: list[dict], ranked: list[dict], best: str | None, original: str) -> None:
    """Persist the ranking in ``compiler_tests`` (the table ROM mode uses), one identical row per sample.

    The table is keyed by function, so a survey shares the same shape as ROM mode's compiler test:
    ``result`` holds the ranked list and the chosen copy, and ``input_digest`` identifies the sample.
    """
    proj = net._project(slug)
    digest = hashlib.sha256("|".join(str(fn["id"]) for fn in funcs).encode("utf-8")).hexdigest()
    payload = json.dumps({"xdk360_survey": True, "ranked": ranked, "best": best,
                          "original": original, "tested": len(funcs)})
    with net.project_store(proj["id"]).tx() as db:
        for fn in funcs:
            db.execute("INSERT OR REPLACE INTO compiler_tests VALUES(?,?,?,?)",
                       (fn["id"], digest, time.time(), payload))


def _stop_requested() -> None:
    """Raise when the user pressed Stop, so a survey of 17 compilers ends instead of running to the last one."""
    from .purpose_work import checkpoint_owner

    checkpoint_owner()


def _project_to_itself(net, slug: str):
    """The project's source tree, held alone (ForkEngine._tree exclusively) for as long as the survey runs.

    The survey regenerates the project's one build.ninja for each XDK copy it tries. Without this, a helper's or
    autopilot's compile that ran meanwhile built with whichever copy was being tried, and its verdict (a match
    recorded or missed) came from a compiler the project doesn't use. Compiles take the tree shared, so they wait
    for the survey and run with the project's own compiler once it has put it back; the survey's own judging
    builds take it again from this thread. No engine (tests): nothing to hold."""
    forks = getattr(net, "forks", None)
    if forks is None:
        return nullcontext()
    return forks._tree(net._project(slug)).exclusive()


def survey_xdk360(net, slug: str, *, limit: int = 12, apply: bool = True,
                  log: Callable[[str], None] = print) -> dict:
    """Run the survey (_survey_xdk360) with the project's builds held until it ends (_project_to_itself)."""
    log("waiting for the project's builds in progress to finish; helpers' compiles wait for the survey")
    with _project_to_itself(net, slug):
        return _survey_xdk360(net, slug, limit=limit, apply=apply, log=log)


def _survey_xdk360(net, slug: str, *, limit: int = 12, apply: bool = True,
                   log: Callable[[str], None] = print) -> dict:
    """Try every registered xdk360 copy on a sample, rank by exact matches, and default to the best.

    ``apply=False`` ranks and records the result without changing the project's compiler. With the
    default ``apply=True`` the best copy is written as the project default (and build.ninja rebuilt) when it
    beat the project's own copy on at least one function, or — on a sample that every copy builds identically and
    a project that has nothing proven to lose — when it is the XDK closest to the game's own link date. A project
    that already reproduces functions keeps the compiler that reproduced them. Copies that ship one identical
    compiler are tried once and scored together (``compiler_groups``). ``limit`` bounds the number of sampled
    functions (each distinct compiler is tried on all of them). Returns a dict with ``ranked`` (best first),
    ``best``, ``original``, ``applied``, ``tested`` and ``linked`` (the game's link day, when known).
    """
    compilers = registered_compilers()
    funcs = sample_functions(net, slug, limit)
    if len(compilers) < 2:
        log(f"X360 compiler survey needs at least two registered XDK copies; found {len(compilers)}.")
        return {"ranked": [], "best": None, "original": None, "applied": False, "tested": len(funcs)}
    if not funcs:
        log("X360 compiler survey found no functions with saved C or a seed to try the XDK copies on.")
        return {"ranked": [], "best": None, "original": None, "applied": False, "tested": 0}

    original = original_compiler(net, slug)
    version = _version(net, slug)
    linked = game_build_time(net, slug)
    groups = compiler_groups(compilers)
    log(f"surveying {len(groups)} distinct XDK compilers among {len(compilers)} registered copies on "
        f"{len(funcs)} functions (project builds with {original}"
        + (f"; the game was linked {_day(linked)}" if linked else "") + ")")
    ranked: list[dict] = []
    try:
        for group in groups:
            _stop_requested()
            own = group[0]
            _set_compiler(net, slug, version, own.version)
            scores = [judge(net, slug, fn, log) for fn in funcs]
            built = [score for score in scores if score is not None]
            matched = sum(1 for score in scores if score == 0)
            mean = round(sum(built) / len(built), 1) if built else None
            for member in group:
                built_time = build_time(member)
                # An XDK built after the game was linked cannot have made it, so those copies rank below the
                # ones that already existed. Unknown dates (a copy with no readable cl.exe) stay undecided.
                fits = None if built_time is None or linked is None else built_time <= linked
                ranked.append({"version": member.version, "matched": matched, "total": len(funcs),
                               "percent": round(100.0 * matched / len(funcs), 1), "score": mean,
                               "built": _day(built_time), "built_ts": built_time, "fits": fits})
            log(f"  {own.version}: {matched}/{len(funcs)} match ({100.0 * matched / len(funcs):.1f}%)"
                + (f", mean distance {mean}" if mean is not None else ", nothing to build")
                + (f"; {len(group) - 1} other copies ship the same compiler" if len(group) > 1 else ""))
    finally:
        # A failed or cancelled survey leaves the project exactly as it was; a successful one still restores
        # here, then applies the winner below so every future build uses it. The restore is the one thing that
        # must still happen when Stop was pressed, so its failure is reported rather than raised.
        try:
            _set_compiler(net, slug, version, original)
        except Exception as error:  # noqa: BLE001  the project's recorded compiler is untouched either way
            log(f"could not put {original} back after the survey: {error}")

    def order(row: dict) -> tuple:
        """Exact matches first, then the copies that bring the sample closest (``score``: 0 is identical), then
        the ones that existed when the game was linked, newest of those first. The distance is what is left when
        no copy is exact; the date is what is left when the distance ties too. ``!= original`` then keeps the
        project's own copy on a tie nothing settles."""
        stamp = row["built_ts"] or 0
        return (-row["matched"], float("inf") if row["score"] is None else row["score"],
                0 if row["fits"] else 1, -stamp if row["fits"] else stamp,
                row["version"] != original, row["version"])

    ranked.sort(key=order)
    best = ranked[0]
    own = next((row for row in ranked if row["version"] == original), None)
    try:
        record(net, slug, funcs, ranked, best["version"], original)
    except (NetworkError, OSError, ValueError) as error:
        log(f"X360 compiler survey could not save its ranking: {error}")

    applied = False
    proven = sum(1 for fn in funcs if (fn.get("matched_source") or "").strip())
    beaten = own is None or best["matched"] > own["matched"]
    if not best["matched"]:
        log("no sampled function matched under any XDK copy, so this ranking is not evidence; "
            "decompile or match a function first, then survey again")
    elif not apply:
        pass
    elif best["version"] == original:
        log(f"the project's own {original} is already the best XDK copy on this sample")
    elif beaten:
        _choose_compiler(net, slug, version, best["version"])  # the only write that records a choice
        applied = True
        log(f"the best XDK copy is {best['version']} ({best['matched']}/{best['total']} match); "
            f"all future builds use it")
    elif not proven:
        # Nothing proven yet, so the date is the only evidence there is: a fresh project starts on the XDK
        # closest to its own game's date instead of whichever copy the registry happened to list first.
        _choose_compiler(net, slug, version, best["version"])  # a fresh project records its starting copy
        applied = True
        log(f"no copy builds this sample differently, so the title's own date decides: {best['version']} "
            f"(compiler built {best['built']}" + (f", the game was linked {_day(linked)}" if linked else "")
            + "); all future builds use it")
    else:
        # A project that already reproduces functions keeps the compiler that reproduced them: a tie here is
        # the title's date guessing, and it must not move work that is already matching away from its compiler.
        log(f"{best['version']} is the XDK closest to the game's own date (compiler built {best['built']}), "
            f"but this project already matches {proven} function(s) with {original}: keeping {original}")
    return {"ranked": ranked, "best": best["version"], "original": original,
            "applied": applied, "tested": len(funcs), "linked": _day(linked)}
