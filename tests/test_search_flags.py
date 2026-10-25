"""Flags searched, not only compilers (search_flags): the C that looks right but won't match is often built with the
wrong flags. Each variant is judged like an attempt and recorded nowhere."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork

from .test_app_engine import CLAMP
from .test_rom import ROM, built  # noqa: F401  (fixture that builds the demo ROM)


def test_the_variants_change_one_flag_each():
    net = RomNetwork(":memory:", ".")
    base = ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]
    variants = net.flag_variants("gcc-2.7.2-kmc", base)
    assert variants[0] == base and ["-G0", "-mips3", "-mgp32", "-mfp32", "-O1"] in variants
    assert ["-G8", "-mips3", "-mgp32", "-mfp32", "-O2"] in variants and [*base, "-fno-strength-reduce"] in variants
    assert all(sum(a != b for a, b in zip(v, base)) <= 1 for v in variants if len(v) == len(base))
    cw = net.flag_variants("mwcps2-3.0.1", ["-O3", "-sdatathreshold", "8"])
    assert ["-O3", "-sdatathreshold", "0"] in cw and ["-O4", "-sdatathreshold", "8"] in cw
    snc = net.flag_variants("pspsnc-1.2.7503", ["-O2", "-G8"])  # SNC has no -Os (it refuses to build with it)
    assert ["-O4", "-G8"] in snc and ["-O2", "-G0"] in snc and ["-O2", "-G8", "-Xsched=0"] in snc
    assert not any("-Os" in v or "-fno-strength-reduce" in v for v in snc)


def test_a_project_with_the_wrong_flags_hears_which_ones_match(built, tmp_path):  # noqa: F811
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent("claude-opus-5.5-1")
    net.split_function(agent, slug, "func_800007C4", "0x800007D0")
    net._update_cfg(slug, lambda cfg: cfg["compiler"].__setitem__(
        "flags", [f if f != "-O2" else "-O1" for f in cfg["compiler"]["flags"]]))  # the project's flags are wrong
    said = net.search_flags(agent, slug, "func_800007D0", source=CLAMP)
    assert "MATCHES with" in said and "-O2" in said.split("MATCHES with")[1].split(".")[0], said
    assert json.loads(net._project(slug)["config"])["compiler"]["flags"][-1] == "-O1"  # it only says, changes nothing
    with pytest.raises(Exception, match="no attempt yet"):
        net.search_flags(agent, slug, "func_800007D0")


@pytest.mark.parametrize("toolchain", ["gcc-2.7.2-kmc", "ee-gcc-2.95.3"])
@pytest.mark.parametrize("debug", [None, "-g", "-g2", "-g3"])
def test_gcc_debug_search_replaces_the_debug_level(toolchain, debug):
    net = RomNetwork(":memory:", ".")
    plain = ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]
    base = plain + ([debug] if debug else [])
    variants = net.flag_variants(toolchain, base)
    assert variants[0] == base
    for flags in (plain, [*plain, "-g"], [*plain, "-g3"]):
        assert flags in variants
    assert all(sum(f.startswith("-g") for f in flags) <= 1 for flags in variants)
    assert len(variants) <= 12


@pytest.mark.parametrize("debug", ["-g", "-g3"])
def test_the_survey_retries_old_misses_with_debug_flags(tmp_path, monkeypatch, debug):
    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    source = "int f(int x) { return x + 1; }"
    flags = ["-G0", "-mips3", "-mgp32", "-mfp32", "-O2"]
    cfg = {"kind": "rom", "platform": "n64", "compiler": {"toolchain": "gcc-2.7.2-kmc", "flags": flags},
           "compiler_survey": {"tried": {str(0x80001000): hashlib.sha1(source.encode()).hexdigest()[:16]}}}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,n_insns,status,best_score,best_source) "
                   "VALUES(?,?,?,?,?,?,?)", (pid, "f", 0x80001000, 16, "open", 160, source))
    monkeypatch.setattr(net, "compiler_map", lambda *a, **k: SimpleNamespace(contradicted=set()))
    monkeypatch.setattr(net, "_suggest_from_map", lambda *a: [])
    judged = []

    def judge(proj, fn, tc, variant, candidate):
        judged.append(variant)
        return debug in variant, None, ""

    monkeypatch.setattr(net, "_judge_with", judge)
    result = net.survey_compilers("demo")
    assert result["confirmed"] == 1
    saved = json.loads(net._project("demo")["config"])
    assert saved["compiler_survey"]["confirmed"][str(0x80001000)] == ["gcc-2.7.2-kmc", [*flags, debug]]
    assert saved["compiler"] == cfg["compiler"]  # detection alone does not change the project's flags
    before = len(judged)
    assert net.survey_compilers("demo")["tried"] == 0
    assert len(judged) == before


def test_installed_compiler_comparison_is_local_and_report_only(tmp_path, monkeypatch):
    from decompnet import toolchains

    net = RomNetwork(tmp_path / "decomp.db", tmp_path)
    source = "int f(int x) { return x + 1; }"
    cfg = {"kind": "rom", "platform": "ps", "compiler": {"toolchain": "gcc-2.7.2-psx", "flags": ["-O2", "-G8"]},
           "auto_compiler": True}
    with net.store.tx() as db:
        pid = db.execute("INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)",
                         ("demo", "Demo", str(tmp_path), json.dumps(cfg))).lastrowid
        db.execute("INSERT INTO functions(project_id,name,vram,n_insns,status,best_score,best_source) "
                   "VALUES(?,?,?,?,?,?,?)", (pid, "f", 0x80010000, 16, "open", 160, source))
    monkeypatch.setattr(net, "compiler_map", lambda *a, **k: SimpleNamespace(contradicted=set()))
    monkeypatch.setattr(toolchains, "installed", lambda tc: {"installed": True} if tc in (
        "gcc-2.7.2-psx", "gcc-2.8.0-psx") else None)
    monkeypatch.setattr(toolchains, "get", lambda tc: SimpleNamespace(supported=True, flags=["-O2", "-G8"]))
    monkeypatch.setattr(toolchains, "runtime_error", lambda tc: None)
    judged = []

    def judge(proj, fn, tc, flags, candidate):
        judged.append(tc)
        return tc == "gcc-2.8.0-psx", None, ""

    monkeypatch.setattr(net, "_judge_with", judge)
    monkeypatch.setattr(net, "_suggest_from_map", lambda *a: pytest.fail("comparison must not apply suggestions"))
    result = net.survey_compilers("demo", all_installed=True)
    assert result["confirmed"] == 1
    assert result["matches"][str(0x80010000)][0] == "gcc-2.8.0-psx"
    assert set(judged) <= {"gcc-2.7.2-psx", "gcc-2.8.0-psx"}
    assert json.loads(net._project("demo")["config"]) == cfg
