"""Shared fingerprint indexing must not wait on another thread's progress synchronization."""
import json
import threading

from decompnet.app.engine import RomNetwork


def test_rom_index_does_not_sync_progress_while_holding_shared_index_lock(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'db.sqlite', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('game', 'Example', 'mips', str(tmp_path), json.dumps(cfg)))
    proj = net.store.one('SELECT * FROM projects WHERE slug=?', 'game')
    monkeypatch.setattr(net.twins, '_rom_prints', lambda _: {})
    finished, errors = threading.Event(), []
    def index():
        try:
            net.twins.index_project(proj)
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    worker = threading.Thread(target=index, daemon=True)
    try:
        # A concurrent progress restore holds this while it verifies code. Its next successful
        # match also needs the shared index, so the index must not acquire this lock in reverse.
        with net.progress._project_lock('game'):
            worker.start()
            assert finished.wait(2), 'fingerprint indexing waited for project synchronization'
    finally:
        worker.join(timeout=3)
    assert not errors
