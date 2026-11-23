"""Tests for lock-free direct MWCC temp-file trial compilation in ForkEngine."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from decompnet.app.forkengine import ForkEngine


def test_direct_compile_cmd_parses_and_caches_ninja_mwcc_command(tmp_path, monkeypatch):
    (tmp_path / "build.ninja").write_text("# dummy\n", encoding="utf-8")
    unit = SimpleNamespace(
        name="main/game/gx",
        source="src/game/gx.c",
        base=tmp_path / "build/REXE01/src/game/gx.o",
        target=tmp_path / "build/REXE01/obj/src/game/gx.o",
    )
    fp = SimpleNamespace(root=tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                '"python" tools/download_tool.py wibo build/tools/wibo\n'
                'build/tools/wibo build/tools/sjiswrap.exe /mwcc/mwcceppc.exe '
                '-O4,p -MMD -c src/game/gx.c -o build/REXE01/src/game && python tools/transform_dep.py a b\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    eng = ForkEngine(SimpleNamespace(store=SimpleNamespace(path=":memory:")))

    cmd1 = eng._direct_compile_cmd(fp, unit)
    cmd2 = eng._direct_compile_cmd(fp, unit)

    assert cmd1 == [
        "build/tools/wibo",
        "build/tools/sjiswrap.exe",
        "/mwcc/mwcceppc.exe",
        "-O4,p",
        "-MMD",
        "-c",
        "src/game/gx.c",
        "-o",
        "build/REXE01/src/game",
    ]
    assert cmd2 == cmd1
    assert len(calls) == 1


def test_direct_compile_cmd_returns_none_without_build_ninja(tmp_path):
    unit = SimpleNamespace(
        name="main/game/gx",
        source="src/game/gx.c",
        base=tmp_path / "build/REXE01/src/game/gx.o",
    )
    fp = SimpleNamespace(root=tmp_path)
    eng = ForkEngine(SimpleNamespace(store=SimpleNamespace(path=":memory:")))
    assert eng._direct_compile_cmd(fp, unit) is None
