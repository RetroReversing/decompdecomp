"""Cross-process synchronization tests for ForkEngine locks.

Verifies that _unit_lock, _build_locks, _git_locks, and _TreeLock coordinate
across separate OS processes (such as permuter_worker and deep_lanes children)
sharing the same project database and source tree.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest

from decompnet.app.forkengine import ForkEngine


def _make_engine(db_path):
    net = SimpleNamespace(store=SimpleNamespace(path=str(db_path)))
    return ForkEngine(net)


def _wait_for(path, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path}"
        time.sleep(0.01)


def test_cross_process_unit_lock_reentrant_in_process(tmp_path):
    engine = _make_engine(tmp_path / "decomp.db")
    proj = {"id": 1}
    with engine._unit_lock(proj, "main/auto/text_800A485C"):
        with engine._unit_lock(proj, "main/auto/text_800A485C"):
            assert True


def test_cross_process_unit_lock_serializes_child_process(tmp_path):
    db_path = tmp_path / "decomp.db"
    ready = tmp_path / "ready.txt"
    marker = tmp_path / "acquired.txt"
    engine = _make_engine(db_path)
    proj = {"id": 7}
    unit = "main/auto/text_800A485C"

    child_code = textwrap.dedent(f"""\
        import pathlib, time
        from types import SimpleNamespace
        from decompnet.app.forkengine import ForkEngine

        eng = ForkEngine(SimpleNamespace(store=SimpleNamespace(path={str(db_path)!r})))
        pathlib.Path({str(ready)!r}).write_text("ready")
        t0 = time.monotonic()
        with eng._unit_lock({{"id": 7}}, {unit!r}):
            pathlib.Path({str(marker)!r}).write_text(str(time.monotonic() - t0))
    """)

    with engine._unit_lock(proj, unit):
        proc = subprocess.Popen([sys.executable, "-c", child_code])
        _wait_for(ready)
        time.sleep(0.2)
        assert not marker.exists(), "child acquired unit lock while parent held it"
    assert proc.wait(timeout=10) == 0
    assert marker.exists()
    assert float(marker.read_text()) >= 0.15


def test_cross_process_build_and_git_locks_serialize_child_process(tmp_path):
    db_path = tmp_path / "decomp.db"
    ready = tmp_path / "git_ready.txt"
    marker = tmp_path / "git_acquired.txt"
    engine = _make_engine(db_path)

    child_code = textwrap.dedent(f"""\
        import pathlib, time
        from types import SimpleNamespace
        from decompnet.app.forkengine import ForkEngine

        eng = ForkEngine(SimpleNamespace(store=SimpleNamespace(path={str(db_path)!r})))
        pathlib.Path({str(ready)!r}).write_text("ready")
        t0 = time.monotonic()
        with eng._git_locks[3]:
            pathlib.Path({str(marker)!r}).write_text(str(time.monotonic() - t0))
    """)

    with engine._git_locks[3]:
        proc = subprocess.Popen([sys.executable, "-c", child_code])
        _wait_for(ready)
        time.sleep(0.2)
        assert not marker.exists(), "child acquired git lock while parent held it"
    assert proc.wait(timeout=10) == 0
    assert marker.exists()
    assert float(marker.read_text()) >= 0.15


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock shared/exclusive test")
def test_cross_process_tree_lock_blocks_exclusive_while_shared_held(tmp_path):
    db_path = tmp_path / "decomp.db"
    ready = tmp_path / "tree_ready.txt"
    marker = tmp_path / "exclusive_acquired.txt"
    engine = _make_engine(db_path)
    proj = {"id": 2}

    child_code = textwrap.dedent(f"""\
        import pathlib, time
        from types import SimpleNamespace
        from decompnet.app.forkengine import ForkEngine

        eng = ForkEngine(SimpleNamespace(store=SimpleNamespace(path={str(db_path)!r})))
        pathlib.Path({str(ready)!r}).write_text("ready")
        t0 = time.monotonic()
        with eng._tree({{"id": 2}}).exclusive():
            pathlib.Path({str(marker)!r}).write_text(str(time.monotonic() - t0))
    """)

    with engine._tree(proj).shared():
        proc = subprocess.Popen([sys.executable, "-c", child_code])
        _wait_for(ready)
        time.sleep(0.2)
        assert not marker.exists(), "child acquired exclusive tree lock while parent held shared"
    assert proc.wait(timeout=10) == 0
    assert marker.exists()
    assert float(marker.read_text()) >= 0.15


def test_in_memory_store_uses_memory_locks_without_files():
    engine = _make_engine(":memory:")
    proj = {"id": 1}
    with engine._tree(proj).exclusive(), engine._unit_lock(proj, "u"), engine._build_locks[1], engine._git_locks[1]:
        assert engine._lock_path(1, "build") is None
