import os
import subprocess
import sys

import pytest

from decompnet import limits


def test_limit_configuration(monkeypatch):
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', '512')
    assert limits.compiler_memory_limit() == 512 * 1024 * 1024
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', 'invalid')
    assert limits.compiler_memory_limit() == 2048 * 1024 * 1024


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS process tree watchdog')
def test_watchdog_stops_memory_hungry_child_and_parent():
    child = 'import time; a=bytearray(80*1024*1024); time.sleep(15)'
    parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",sys.argv[1]]); time.sleep(15)'
    proc = subprocess.Popen([sys.executable, '-c', parent, child], start_new_session=True)
    try:
        with limits.CompilerMemoryWatchdog(proc, limit=40 * 1024 * 1024) as watchdog:
            proc.wait(timeout=10)
        assert watchdog.exceeded
        assert 'memory limit' in watchdog.message()
    finally:
        if proc.poll() is None:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_process_tree_parses_descendants(monkeypatch):
    class Result:
        stdout = '10 1 100\n11 10 200\n12 11 300\n90 1 900\n'
    monkeypatch.setattr(limits.subprocess, 'run', lambda *args, **kwargs: Result())
    assert dict(limits._process_memory_tree(10)) == {10: 102400, 11: 204800, 12: 307200}


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS process tree watchdog')
def test_compiler_reports_memory_stop(tmp_path, monkeypatch):
    from decompnet.compiler import CompilerConfig, _exec
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', '40')
    script = 'import time; a=bytearray(80*1024*1024); time.sleep(15)'
    result = _exec(CompilerConfig(cmd=[]), tmp_path, [sys.executable, '-c', script], 10)
    assert result.returncode != 0
    assert 'exceeded the 40 MiB memory limit' in result.stderr


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS process tree watchdog')
def test_imported_project_build_reports_memory_stop(tmp_path, monkeypatch):
    from decompnet.forks.project import ForkProject
    monkeypatch.setenv('DECOMP_COMPILER_MEMORY_MB', '40')
    monkeypatch.setattr(ForkProject, '_docker_only_macos', lambda _: False)
    project = ForkProject(tmp_path)
    script = 'import time; a=bytearray(80*1024*1024); time.sleep(15)'
    ok, output = project.run([sys.executable, '-c', script])
    assert not ok
    assert 'exceeded the 40 MiB memory limit' in output
