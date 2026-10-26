"""Captured judge evidence is immutable; explanations remain untrusted review notes."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import tools
from decompnet.app.forkengine import ForkEngine
from decompnet.network import Network, NetworkError


@pytest.fixture
def setup(tmp_path):
    net = Network(tmp_path / 'app.db')
    winner = net.authenticate(net.register_agent('solver'))
    other = net.authenticate(net.register_agent('other'))
    cfg = {'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']}}
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,config,big_endian,root) VALUES(?,?,?,?,?)',
                         ('demo', 'Demo', json.dumps(cfg), 1, str(tmp_path))).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns,status,unit,vram,target,meta) '
                         'VALUES(?,?,?,?,?,?,?,?,?)', (pid, 'target', 16, 4, 'open', '', 0x80001000, b'original', '{}')).lastrowid
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)', (pid, '', 1, 'typedef int word;'))
    return net, winner, other, pid, fid


def fn(net, fid):
    return net.store.one('SELECT * FROM functions WHERE id=?', fid)


def match(setup, note=''):
    net, winner, other, pid, fid = setup
    net._record(fn(net, fid), other, 20, 'int target(void) { return 1; }\n', 1)
    net._record(fn(net, fid), winner, 10, 'int target(void) { return 2; }\n', 1)
    net._record(fn(net, fid), winner, 0, 'int target(void) { return 3; }\n', 1, matched=True)
    return net.repair_feedback.listing('demo')['examples'][0]['id']


def test_automatic_evidence_chooses_closest_failed_candidate_and_retains_original(setup):
    net, winner, _, _, fid = setup
    case = match(setup)
    data = net.repair_feedback.detail('demo', case)['evidence']
    assert data['before']['score'] == 10 and 'return 2' in data['before']['source']
    assert 'return 3' in data['after']['source'] and '-int target' in data['diff']
    assert data['after']['header'] == 'typedef int word;'
    assert data['after']['settings']['compiler']['flags'] == ['-O2']
    assert data['judge'] == 'linked byte-identical' and data['target_sha256']
    # Polishing neither duplicates cases nor changes the earlier winning attempt.
    net._record(fn(net, fid), winner, 0, 'int target(void) { return 3; } /* polished */', 1, matched=True)
    assert len(net.repair_feedback.listing('demo')['examples']) == 1
    assert 'polished' not in net.repair_feedback.detail('demo', case)['evidence']['after']['source']


def test_annotations_reviews_and_exports_are_separate_from_judge(setup):
    net, winner, other, pid, fid = setup
    case = match(setup)
    with pytest.raises(NetworkError, match='by you'):
        net.repair_feedback.report(other, 'demo', 'target', 'guess')
    net.repair_feedback.report(winner, 'demo', 'target', 'Corrected a literal.', 'constants', 'Try the diff-proven constant.')
    data = net.repair_feedback.detail('demo', case)
    assert data['category'] == 'constants' and data['status'] == 'new'
    net.repair_feedback.review('demo', case, 'reviewed')
    assert net.repair_feedback.listing('demo', category='constants', status='reviewed')['groups'][0]['count'] == 1
    summary = json.loads(''.join(net.repair_feedback.export('demo')))
    full = json.loads(''.join(net.repair_feedback.export('demo', include_source=True)))
    assert 'source' not in summary['examples'][0]['evidence']['after']
    assert 'diff' not in summary['examples'][0]['evidence']
    assert 'return 3' in full['examples'][0]['evidence']['after']['source']
    assert fn(net, fid)['status'] == 'matched'
    with pytest.raises(NetworkError):
        net.repair_feedback.review('demo', case, 'enable_rule')


def test_inline_note_only_survives_a_new_verified_match(setup, monkeypatch):
    net, winner, _, pid, fid = setup
    def attempt(agent, name, source, slug):
        net._record(fn(net, fid), agent, 0 if source == 'good' else 5, source, 1, matched=source == 'good')
        return 'result'
    monkeypatch.setattr(net, 'compile_and_diff', attempt, raising=False)
    tools._compile(net, winner, 'demo', 'target', 'bad', repair_note='Not proven yet')
    assert not net.repair_feedback.listing('demo')['examples']
    result = tools._compile(net, winner, 'demo', 'target', 'good', 'Fixed argument type.', 'types', 'Try signedness variants.')
    assert 'explanation saved' in result
    row = net.repair_feedback.listing('demo')['examples'][0]
    assert row['explanation'] == 'Fixed argument type.'
    tools._compile(net, winner, 'demo', 'target', 'good', 'Polished.', 'other')
    assert net.repair_feedback.listing('demo')['examples'][0]['explanation'] == 'Fixed argument type.'


def test_first_try_and_changed_context_are_not_presented_as_isolated_code_repairs(setup):
    net, winner, _, pid, fid = setup
    net._record(fn(net, fid), winner, 0, 'first try', 1, matched=True)
    data = net.repair_feedback.detail('demo', 1)['evidence']
    assert data['before'] is None
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open' WHERE id=?", (fid,))
    net._record(fn(net, fid), winner, 9, 'before', 1)
    with net.store.tx() as db:
        cfg = json.loads(db.execute('SELECT config FROM projects WHERE id=?', (pid,)).fetchone()['config'])
        cfg['compiler']['flags'] = ['-O1']
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps(cfg), pid))
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)', (pid, '', 2, 'typedef unsigned int word;'))
    net._record(fn(net, fid), winner, 0, 'after', 2, matched=True)
    latest = net.repair_feedback.detail('demo', net.repair_feedback.listing('demo')['examples'][0]['id'])['evidence']
    assert latest['before']['header'] != latest['after']['header']
    assert latest['before']['settings']['compiler']['flags'] != latest['after']['settings']['compiler']['flags']


def test_imported_unit_diff_matches_are_captured_with_explicit_build_context_limits(setup):
    net, winner, _, pid, fid = setup
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=? WHERE id=?', (json.dumps({'kind': 'fork', 'platform': 'gc_wii', 'compiler': 'Metrowerks'}), pid))
    engine = SimpleNamespace(net=net, _find=lambda text, names: (None, (0, len(text))),
                             _names=lambda meta, row: [], _meta=lambda row: {})
    with net.store.tx() as db:
        ForkEngine._matched(engine, db, net._project('demo'), fn(net, fid), winner, 'void target(void) {}')
    data = net.repair_feedback.detail('demo', 1)['evidence']
    assert data['judge'] == 'objdiff 100%'
    assert data['after']['source'] == 'void target(void) {}'
    assert not data['after']['settings']['per_file_flags_recorded']


def test_delete_cleans_evidence_and_attempt_settings(setup):
    net, winner, _, pid, fid = setup
    match(setup)
    with net.store.tx() as db:
        db.execute('DELETE FROM attempts WHERE function_id=?', (fid,))
    assert net.store.one('SELECT COUNT(*) AS n FROM repair_examples')['n'] == 0
    assert net.store.one('SELECT COUNT(*) AS n FROM repair_attempt_settings')['n'] == 0


def test_paging_comparison_filter_and_manual_review_notes(setup):
    net, winner, _, pid, fid = setup
    net._record(fn(net, fid), winner, 0, 'first', 1, matched=True)
    assert not net.repair_feedback.listing('demo', comparable=True)['examples']
    for number in range(28):
        with net.store.tx() as db:
            db.execute("UPDATE functions SET status='open' WHERE id=?", (fid,))
        net._record(fn(net, fid), winner, 10, f'failed {number}', 1)
        net._record(fn(net, fid), winner, 0, f'matched {number}', 1, matched=True)
    page = net.repair_feedback.listing('demo', comparable=True)
    assert len(page['examples']) == 25 and page['more']
    older = net.repair_feedback.listing('demo', before=page['examples'][-1]['id'], comparable=True)
    assert len(older['examples']) == 3 and not older['more']
    case = page['examples'][0]['id']
    net.repair_feedback.review('demo', case, 'reviewed', 'casts', 'Try as an AST rewrite; implementation PR pending.')
    data = net.repair_feedback.detail('demo', case)
    assert data['category'] == 'casts' and 'AST rewrite' in data['review_note']
    net.repair_feedback.report(winner, 'demo', 'target', 'Revised explanation.', 'types')
    assert net.repair_feedback.detail('demo', case)['status'] == 'new'


def test_deleted_old_attempt_cannot_be_replaced_with_unrelated_reused_id(setup):
    net, winner, _, pid, fid = setup
    case = match(setup)
    before = net.repair_feedback.detail('demo', case)['before_attempt_id']
    with net.store.tx() as db:
        db.execute('DELETE FROM attempts WHERE id=?', (before,))
        db.execute('INSERT INTO attempts(id,function_id,source) VALUES(?,?,?)', (before, fid, 'unrelated reused record'))
    assert net.repair_feedback.detail('demo', case)['evidence']['before']['source'] is None


def test_mcp_and_api_accept_inline_feedback_without_an_extra_model_call(setup, monkeypatch):
    import anyio

    from decompnet.agents.api import api_tool_schemas
    net, winner, _, _, fid = setup
    def attempt(agent, name, source, slug):
        net._record(fn(net, fid), agent, 0, source, 1, matched=True)
        return 'matched'
    monkeypatch.setattr(net, 'compile_and_diff', attempt, raising=False)
    monkeypatch.setattr(tools, '_named', lambda *args: args[-1])
    mcp = tools.build_mcp(net, lambda context: (winner, 'demo'))
    anyio.run(mcp.call_tool, 'compile_and_diff', {'function': 'target', 'source': 'winning C',
                                               'repair_note': 'Changed signedness.', 'repair_category': 'types',
                                               'repair_automation': 'Try a signedness repair.'})
    row = net.repair_feedback.listing('demo')['examples'][0]
    assert row['explanation'] == 'Changed signedness.'
    schema = {row['function']['name']: row['function'] for row in api_tool_schemas(names=['compile_and_diff', 'report_repair'])}
    assert 'repair_note' in schema['compile_and_diff']['parameters']['properties']
    assert 'repair_note' not in schema['compile_and_diff']['parameters']['required']
    assert 'report_repair' in schema


def test_header_snapshot_records_global_and_unit_context_scopes(setup):
    net, winner, _, pid, fid = setup
    with net.store.tx() as db:
        db.execute('UPDATE functions SET unit=? WHERE id=?', ('main', fid))
    net._record(fn(net, fid), winner, 10, 'before', 1)
    with net.store.tx() as db:
        db.execute('INSERT INTO context_versions(project_id,unit,version,header) VALUES(?,?,?,?)',
                   (pid, 'main', 2, 'typedef unsigned int word;'))
    net._record(fn(net, fid), winner, 0, 'after', 2, matched=True)
    evidence = net.repair_feedback.detail('demo', 1)['evidence']
    assert evidence['before']['header'] == 'typedef int word;'
    assert evidence['after']['header'] == 'typedef unsigned int word;'
