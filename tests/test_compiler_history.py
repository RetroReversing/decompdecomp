"""Durable per-function comparisons resume bulk work without changing game settings."""
import json
from types import SimpleNamespace

import pytest

from decompnet import toolchains
from decompnet.app.engine import RomNetwork


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('demo', 'Demo', str(tmp_path), json.dumps(cfg))).lastrowid
        for i, state in enumerate(['open', 'open', 'matched', 'asm']):
            db.execute('INSERT INTO functions(project_id,name,vram,size,n_insns,status,best_score,best_source) VALUES(?,?,?,?,?,?,?,?)',
                       (pid, f'f{i}', 0x80001000+i*32, 16, 4, state, 200, f'int f{i}(void){{return 1;}}'))
    profiles = [('gcc-2.7.2-kmc', ('-O2',)), ('sn-n64-281', ('-O1',))]
    monkeypatch.setattr(net, '_comparison_candidates', lambda *a: profiles)
    monkeypatch.setattr(net, 'compiler_map', lambda *a: SimpleNamespace(contradicted=set()))
    monkeypatch.setattr(toolchains, 'installed', lambda _: {'parts': ['pinned']})
    calls = []
    def judge(proj, fn, tc, flags, source):
        calls.append(fn['name'])
        return False, SimpleNamespace(percent=80.0, score=80), ''
    monkeypatch.setattr(net, '_judge_with', judge)
    return net, profiles, calls, tmp_path


def test_bulk_persists_short_functions_and_resumes_after_restart(comparison, monkeypatch):
    net, profiles, calls, path = comparison
    first = net.survey_compilers('demo', all_installed=True, untested_only=True, limit=1)
    assert first['tried'] == 1
    assert net.store.one('SELECT COUNT(*) AS n FROM compiler_tests')['n'] == 1
    reopened = RomNetwork(path / 'app.db', path)
    monkeypatch.setattr(reopened, '_comparison_candidates', net._comparison_candidates)
    monkeypatch.setattr(reopened, 'compiler_map', net.compiler_map)
    monkeypatch.setattr(reopened, '_judge_with', net._judge_with)
    second = reopened.survey_compilers('demo', all_installed=True, untested_only=True)
    assert second['tried'] == 1 and second['skipped'] == 1
    assert set(calls) == {'f0', 'f1'}
    third = reopened.survey_compilers('demo', all_installed=True, untested_only=True)
    assert third['tried'] == 0 and third['skipped'] == 2
    fn = reopened._function('f0', 'demo')
    history = reopened.compiler_test_result(reopened.project_row('demo'), fn)
    assert history['completed_at'] and not history['stale']
    assert len(history['comparison']['ranked']) == 2
    assert reopened.survey_compilers('demo', all_installed=True, untested_only=False)['tried'] == 2


def test_changed_source_context_and_inventory_invalidate_history(comparison):
    net, profiles, _, _ = comparison
    net.survey_compilers('demo', all_installed=True, function='f0')
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f0(void){return 2;}' WHERE name='f0'")
    fn = net._function('f0', 'demo')
    assert net.compiler_test_result(net.project_row('demo'), fn)['stale']
    assert net.survey_compilers('demo', all_installed=True, untested_only=True)['tried'] == 2
    with net.store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)',
                   (fn['project_id'], '', 1, 'typedef int s32;'))
    assert net.compiler_test_result(net.project_row('demo'), fn)['stale']
    net.survey_compilers('demo', all_installed=True, function='f0')
    profiles.append(('gcc-2.7.2-kmc', ('-O3',)))
    assert net.compiler_test_result(net.project_row('demo'), fn)['stale']


def test_interrupted_function_is_not_marked_completed(comparison, monkeypatch):
    net, _, _, _ = comparison
    def fail(*a):
        raise RuntimeError('interrupted')
    monkeypatch.setattr(net, '_judge_with', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        net.survey_compilers('demo', all_installed=True, function='f0')
    assert net.store.one('SELECT COUNT(*) AS n FROM compiler_tests')['n'] == 0


def test_explicit_non_improving_choice_is_revalidated_and_rebases_score(comparison, monkeypatch):
    net, _, _, _ = comparison
    monkeypatch.setattr(net, '_judge_with', lambda *a: (False, SimpleNamespace(score=300), ''))
    applied = []
    monkeypatch.setattr(net, 'set_compiler_ranges', lambda *a: applied.append(a))
    monkeypatch.setattr(net, 'try_quietly', lambda *a: None)
    net.apply_function_compiler('demo', 'f0', 'sn-n64-281', ['-O1'], allow_non_improving=True)
    assert applied[0][2:] == ('sn-n64-281', ['-O1'])
    fn = net._function('f0', 'demo')
    assert fn['best_score'] == 300 and 'return 1' in fn['best_source']
    monkeypatch.setattr(net, '_judge_with', lambda *a: (False, None, 'compile failure'))
    with pytest.raises(Exception, match='could not compile'):
        net.apply_function_compiler('demo', 'f0', 'sn-n64-281', ['-O3'], allow_non_improving=True)
    assert len(applied) == 1
