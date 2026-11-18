"""The X360 automatic XDK survey, kept synthetic: no Wine, Ghidra, real project or real compiler.

The build/judge, the compiler registry and the persistence are replaced with fakes so the ranking,
the default switch and the restore-on-failure guarantee can be checked without a real XDK.
"""

import pytest

from decompnet.app import xdk360_survey as survey

ORIGINAL = "xdk360-000"
FUNCS = [{"id": i + 1, "name": f"fn{i}", "vram": 0x80001000 + 16 * i,
          "best_source": f"int fn{i}(void) {{ return {i}; }}", "seed": None} for i in range(3)]


def wire(monkeypatch, *, matches, original=ORIGINAL, fail_at=None, recorded=None, versions=None):
    """Patch the survey's seams with a fake registry, build switch, judge and recorder.

    ``matches(current, fn)`` decides whether ``fn`` matches while ``current`` is the project compiler;
    ``fail_at`` makes the judge raise on that function name to simulate a failed build.
    """
    versions = versions or [ORIGINAL, "xdk360-111", "xdk360-222"]
    state = {"current": None, "set": []}
    compilers = [type("C", (), {"version": v})() for v in versions]

    monkeypatch.setattr(survey, "registered_compilers", lambda: compilers)
    monkeypatch.setattr(survey, "sample_functions", lambda net, slug, limit: FUNCS[:limit])
    monkeypatch.setattr(survey, "original_compiler", lambda net, slug: original)
    monkeypatch.setattr(survey, "_version", lambda net, slug: "game")

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


def test_too_few_copies_or_no_sources_is_a_noop(monkeypatch):
    wire(monkeypatch, matches=lambda current, fn: True, versions=[ORIGINAL])
    out = survey.survey_xdk360(None, "dash-dash", log=lambda line: None)
    assert out["ranked"] == [] and out["applied"] is False
    monkeypatch.setattr(survey, "sample_functions", lambda net, slug, limit: [])
    out = survey.survey_xdk360(None, "dash-dash", log=lambda line: None)
    assert out["tested"] == 0 and out["ranked"] == []
