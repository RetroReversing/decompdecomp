"""The X360 automatic XDK survey, kept synthetic: no Wine, Ghidra, real project or real compiler.

The build/judge, the compiler registry and the persistence are replaced with fakes so the ranking,
the default switch and the restore-on-failure guarantee can be checked without a real XDK.
"""

from datetime import UTC, datetime

import pytest

from decompnet.app import xdk360_survey as survey

ORIGINAL = "xdk360-000"
FUNCS = [{"id": i + 1, "name": f"fn{i}", "vram": 0x80001000 + 16 * i,
          "best_source": f"int fn{i}(void) {{ return {i}; }}", "seed": None} for i in range(3)]


def stamp(year: int, month: int, day: int) -> float:
    """A PE TimeDateStamp, which is UTC seconds since 1970."""
    return datetime(year, month, day, tzinfo=UTC).timestamp()


def wire(monkeypatch, *, matches, original=ORIGINAL, fail_at=None, recorded=None, versions=None,
         built=None, linked=None):
    """Patch the survey's seams with a fake registry, build switch, judge and recorder.

    ``matches(current, fn)`` decides whether ``fn`` matches while ``current`` is the project compiler;
    ``fail_at`` makes the judge raise on that function name to simulate a failed build. ``built`` maps a
    version to its compiler's PE stamp and ``linked`` is the game's, driving the date tie-break.
    """
    versions = versions or [ORIGINAL, "xdk360-111", "xdk360-222"]
    state = {"current": None, "set": []}
    compilers = [type("C", (), {"version": v})() for v in versions]

    monkeypatch.setattr(survey, "registered_compilers", lambda: compilers)
    monkeypatch.setattr(survey, "sample_functions", lambda net, slug, limit: FUNCS[:limit])
    monkeypatch.setattr(survey, "original_compiler", lambda net, slug: original)
    monkeypatch.setattr(survey, "_version", lambda net, slug: "game")
    monkeypatch.setattr(survey, "build_time", lambda own: (built or {}).get(own.version))
    monkeypatch.setattr(survey, "game_build_time", lambda net, slug: linked)

    def set_compiler(net, slug, version, compiler):
        state["set"].append(compiler)
        state["current"] = compiler

    monkeypatch.setattr(survey, "_set_compiler", set_compiler)

    def judge(net, slug, fn, log):
        if fail_at is not None and fn["name"] == fail_at:
            raise RuntimeError("Wine build failed")
        return bool(matches(state["current"], fn))

    monkeypatch.setattr(survey, "judge", judge)

    def record(net, slug, funcs, ranked, best, original_compiler):
        if recorded is not None:
            recorded.append({"ranked": ranked, "best": best, "original": original_compiler, "funcs": funcs})

    monkeypatch.setattr(survey, "record", record)
    return state


def test_every_copy_tried_ranked_best_applied_and_recorded(monkeypatch):
    recorded = []

    # xdk360-222 matches every function, 111 matches one, the project's own matches none.
    scores = {ORIGINAL: 0, "xdk360-111": 1, "xdk360-222": 3}

    def matches(current, fn):
        return FUNCS.index(fn) < scores[current]

    state = wire(monkeypatch, matches=matches, recorded=recorded)
    out = survey.survey_xdk360(None, "dash-dash", limit=3, log=lambda line: None)

    assert [r["version"] for r in out["ranked"]] == ["xdk360-222", "xdk360-111", ORIGINAL]
    assert out["ranked"][0]["matched"] == 3 and out["ranked"][0]["percent"] == 100.0
    assert out["ranked"][1]["matched"] == 1 and round(out["ranked"][1]["percent"], 1) == 33.3
    assert all(r["total"] == 3 for r in out["ranked"])
    assert out["best"] == "xdk360-222" and out["original"] == ORIGINAL
    assert out["applied"] is True and out["tested"] == 3
    # each copy was installed in turn, the original was restored, then the winner was applied
    assert state["set"] == [ORIGINAL, "xdk360-111", "xdk360-222", ORIGINAL, "xdk360-222"]
    assert len(recorded) == 1 and recorded[0]["best"] == "xdk360-222"
    assert recorded[0]["funcs"] == FUNCS


def test_failed_build_restores_original_and_records_nothing(monkeypatch):
    recorded = []
    state = wire(monkeypatch, matches=lambda current, fn: True, fail_at="fn1", recorded=recorded)
    with pytest.raises(RuntimeError, match="Wine build failed"):
        survey.survey_xdk360(None, "dash-dash", limit=3, log=lambda line: None)
    assert state["set"][-1] == ORIGINAL  # the project is left exactly as it was
    assert "xdk360-111" not in state["set"]  # the survey stopped at the first copy's failure
    assert recorded == []


def test_apply_false_ranks_without_changing_default(monkeypatch):
    state = wire(monkeypatch, matches=lambda current, fn: current == "xdk360-222")
    out = survey.survey_xdk360(None, "dash-dash", limit=3, apply=False, log=lambda line: None)
    assert out["best"] == "xdk360-222" and out["applied"] is False
    assert state["set"][-1] == ORIGINAL  # only the survey switches and the final restore
    assert state["set"] == [ORIGINAL, "xdk360-111", "xdk360-222", ORIGINAL]


def test_tie_keeps_the_projects_own_copy(monkeypatch):
    state = wire(monkeypatch, matches=lambda current, fn: True)  # every copy matches all three
    out = survey.survey_xdk360(None, "dash-dash", limit=3, log=lambda line: None)
    assert out["best"] == ORIGINAL and out["applied"] is False
    assert state["set"][-1] == ORIGINAL


def test_copies_shipping_one_compiler_are_grouped(tmp_path):
    """Two XDK setup media that ship the identical cl.exe are one compiler: 30 copies can be 13 builds."""
    same = tmp_path / "cl.exe"
    same.write_bytes(b"one compiler")
    other = tmp_path / "other" / "cl.exe"
    other.parent.mkdir()
    other.write_bytes(b"another compiler")
    copies = [type("C", (), {"version": v, "exe": p})() for v, p in
              [("a", same), ("b", same), ("c", other)]]
    groups = survey.compiler_groups(copies)
    assert [len(g) for g in groups] == [2, 1]
    assert [own.version for own in groups[0]] == ["a", "b"]


def test_date_picks_the_xdk_that_existed_when_the_game_was_linked(monkeypatch):
    """When every copy builds the sample identically, the newest XDK already released when the game was
    linked is the title's own date pointing at its compiler -- and it is applied, since no copy beat it."""
    linked = stamp(2012, 11, 15)
    built = {ORIGINAL: stamp(2006, 10, 9), "xdk360-111": stamp(2012, 10, 1),
             "xdk360-222": stamp(2013, 2, 1)}  # 222 shipped after the game: it cannot have built it
    lines = []
    state = wire(monkeypatch, matches=lambda current, fn: True, built=built, linked=linked)
    out = survey.survey_xdk360(None, "dash-dash", limit=3, log=lines.append)

    assert [r["version"] for r in out["ranked"]] == ["xdk360-111", ORIGINAL, "xdk360-222"]
    assert out["ranked"][0]["fits"] is True and out["ranked"][2]["fits"] is False
    assert out["ranked"][0]["built"] == "2012-10-01" and out["linked"] == "2012-11-15"
    assert out["best"] == "xdk360-111" and out["applied"] is True
    assert state["set"][-1] == "xdk360-111"
    assert any("the title's own date decides" in line for line in lines)


def test_a_match_win_still_beats_the_date(monkeypatch):
    """Evidence outranks the date: a copy that reproduces more functions wins even if it is the wrong era."""
    built = {ORIGINAL: stamp(2012, 1, 1), "xdk360-111": stamp(2012, 10, 1), "xdk360-222": stamp(2006, 1, 1)}
    wire(monkeypatch, matches=lambda current, fn: current == "xdk360-222", built=built, linked=stamp(2012, 11, 15))
    out = survey.survey_xdk360(None, "dash-dash", limit=3, log=lambda line: None)
    assert out["ranked"][0]["version"] == "xdk360-222" and out["ranked"][0]["matched"] == 3
    assert out["best"] == "xdk360-222" and out["applied"] is True


def test_a_project_with_proven_matches_keeps_its_compiler(monkeypatch):
    """A tie is the date guessing, so it must not move a project that already reproduces functions away from
    the compiler that reproduced them; the date-closest copy is named in the log instead."""
    linked = stamp(2012, 11, 15)
    built = {ORIGINAL: stamp(2006, 10, 9), "xdk360-111": stamp(2012, 10, 1), "xdk360-222": stamp(2013, 2, 1)}
    lines = []
    state = wire(monkeypatch, matches=lambda current, fn: True, built=built, linked=linked)
    proven = [dict(fn, matched_source=fn["best_source"]) for fn in FUNCS]
    monkeypatch.setattr(survey, "sample_functions", lambda net, slug, limit: proven[:limit])

    out = survey.survey_xdk360(None, "dash-dash", limit=3, log=lines.append)

    assert out["ranked"][0]["version"] == "xdk360-111"  # still the date's pick in the ranking
    assert out["best"] == "xdk360-111" and out["applied"] is False
    assert state["set"][-1] == ORIGINAL  # nothing was left switched
    assert any(f"keeping {ORIGINAL}" in line for line in lines)


def test_no_matches_is_not_evidence(monkeypatch):
    """Every copy failing the whole sample says nothing about which is right: keep the project's own copy."""
    state = wire(monkeypatch, matches=lambda current, fn: False,
                 built={ORIGINAL: stamp(2006, 1, 1), "xdk360-111": stamp(2012, 10, 1)}, linked=stamp(2012, 11, 15))
    lines = []
    out = survey.survey_xdk360(None, "dash-dash", limit=3, log=lines.append)
    assert out["applied"] is False and out["tested"] == 3
    assert state["set"][-1] == ORIGINAL
    assert any("not evidence" in line for line in lines)


def test_too_few_copies_or_no_sources_is_a_noop(monkeypatch):
    wire(monkeypatch, matches=lambda current, fn: True, versions=[ORIGINAL])
    out = survey.survey_xdk360(None, "dash-dash", log=lambda line: None)
    assert out["ranked"] == [] and out["applied"] is False
    monkeypatch.setattr(survey, "sample_functions", lambda net, slug, limit: [])
    out = survey.survey_xdk360(None, "dash-dash", log=lambda line: None)
    assert out["tested"] == 0 and out["ranked"] == []
