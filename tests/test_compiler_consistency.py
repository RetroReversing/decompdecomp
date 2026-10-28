"""Compiler changes protect every match and publish scores only for validated settings."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.engine import RomNetwork
from decompnet.app.function_names import canonical_source
from decompnet.network import NetworkError


@pytest.fixture
def net(tmp_path, monkeypatch):
    n = RomNetwork(tmp_path / 'app.db', tmp_path)
    cfg = {'kind': 'rom', 'platform': 'n64', 'compiler': {'toolchain': 'ido-5.3', 'flags': ['-O2'], 'timeout': 20}}
    with n.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('demo', 'Demo', str(tmp_path), json.dumps(cfg))).lastrowid
        for i in range(85):
            db.execute('INSERT INTO functions(project_id,name,vram,size,status,matched_source,best_source,best_score,seed) VALUES(?,?,?,?,?,?,?,?,?)',
                       (pid, f'f{i}', 0x80001000+i*16, 16, 'matched' if i < 84 else 'open',
                        f'int f{i}(void){{return 1;}}' if i < 84 else None, f'int f{i}(void){{return 1;}}', 0 if i < 84 else 20, 'draft'))
    monkeypatch.setattr(n, '_judge_with', lambda *a: (False, SimpleNamespace(score=500), ''))
    return n


def test_manual_change_checks_every_match_and_leaves_no_trial_side_effects(net, monkeypatch):
    before = net.project_row('demo')['config']
    seen = []
    def regression(proj, header, only, **kw):
        assert net.project_row('demo')['config'] == before
        assert net.cfg(proj)['compiler']['flags'] == ['-O1']
        seen.extend(f['name'] for f in only)
        return [('f83', 'mismatch')]
    monkeypatch.setattr(net, 'regression_test', regression)
    with pytest.raises(NetworkError, match="wouldn't match"):
        net.set_compiler('demo', 'ido-5.3', ['-O1'], guard=False)
    assert len(seen) == 84 and 'f83' in seen
    assert net.project_row('demo')['config'] == before
    assert net._function('f84', 'demo')['seed'] == 'draft'
    assert net.store.one('SELECT COUNT(*) AS n FROM events')['n'] == 0


def test_success_rebases_open_scores_and_preserves_range_matches(net, monkeypatch):
    net._update_cfg('demo', lambda c: c.__setitem__('compiler_ranges', [
        {'start': 0x80001000, 'end': 0x80001010, 'toolchain': 'ido-5.3', 'flags': ['-O3']}]))
    def regression(proj, header, only, **kw):
        assert len(only) == 83 and all(f['name'] != 'f0' for f in only)
        return []
    monkeypatch.setattr(net, 'regression_test', regression)
    net.set_compiler('demo', 'ido-5.3', ['-O1'])
    assert net._function('f84', 'demo')['best_score'] == 500
    assert net._function('f84', 'demo')['seed'] is None
    assert net._function('f0', 'demo')['status'] == 'matched'


def test_concurrent_match_prevents_publishing_old_verdicts(net, monkeypatch):
    def regression(*a, **kw):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source='new code' WHERE name='f84'")
        return []
    monkeypatch.setattr(net, 'regression_test', regression)
    with pytest.raises(NetworkError, match='changed during'):
        net.set_compiler('demo', 'ido-5.3', ['-O1'])
    assert net.cfg(net.project_row('demo'))['compiler']['flags'] == ['-O2']
    assert net._function('f84', 'demo')['best_source'] == 'new code'


def test_range_changes_use_the_same_private_validation_and_rebase(net, monkeypatch):
    monkeypatch.setattr(net, 'regression_test', lambda *a, **kw: [])
    net.set_compiler_ranges('demo', [[0x80001540, 0x80001550]], 'ido-5.3', ['-O3'])
    assert net._function('f84', 'demo')['best_score'] == 500
    assert net._function('f83', 'demo')['seed'] == 'draft'


def test_old_function_names_replay_without_changing_literals_or_formatting():
    fn = {'name': 'get_block', 'vram': 0x80001234, 'meta': json.dumps({'naming': {'original_name': 'func_80001234', 'previous_name': 'block'}})}
    source = '/* func_80001234 */\nchar *s="func_80001234";\nint func_80001234(void)\n{ return block(); }\n'
    assert canonical_source(fn, source) == '/* func_80001234 */\nchar *s="func_80001234";\nint get_block(void)\n{ return get_block(); }\n'


def test_replay_accepts_slim_rows_from_range_checks():
    assert canonical_source({'name': 'block', 'vram': 0x80001234}, 'int func_80001234(void) { return 1; }') == 'int block(void) { return 1; }'


def test_in_flight_old_profile_attempt_is_saved_without_stale_green(net):
    fn = net._function('f84', 'demo')
    agent, _ = net.local_agent('worker')
    with pytest.raises(NetworkError, match='attempt is saved'):
        net._record(fn, agent, 0, 'new candidate', 0, matched=True,
                    effective_settings={'compiler': {'toolchain': 'ido-5.3', 'flags': ['-O1']}})
    current = net._function('f84', 'demo')
    assert current['status'] == 'open' and current['best_score'] == 20
    assert net.store.one('SELECT source FROM attempts')['source'] == 'new candidate'


def test_range_check_does_not_block_unrelated_function_progress(net, monkeypatch):
    def regression(*a, **kw):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET best_source='unrelated new code' WHERE name='f84'")
        return []
    monkeypatch.setattr(net, 'regression_test', regression)
    net.set_compiler_ranges('demo', [[0x80001000, 0x80001010]], 'ido-5.3', ['-O3'])
    assert net._function('f84', 'demo')['best_source'] == 'unrelated new code'
    assert len(net.cfg(net.project_row('demo'))['compiler_ranges']) == 1
