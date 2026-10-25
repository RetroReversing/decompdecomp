"""Similarity can propose C without tokens, but only the ordinary judge can accept it."""

import json

import pytest

from decompnet.app import similar_matches as sm
from decompnet.app.engine import RomNetwork
from decompnet.app.twins import Job

from .test_app_engine import DAMAGE
from .test_rom import ROM, built  # noqa: F401
from .test_twins_across_games import copy_project


def code(value, big=True):
    return b"".join(w.to_bytes(4, "big" if big else "little") for w in
                    [0x24820000 | value, 0x24420002, 0x24420003, 0x24420004,
                     0x24420005, 0x24420006, 0x03e00008, 0])


@pytest.mark.parametrize("big", [True, False])
def test_literal_changes_leave_comments_strings_and_symbol_names_alone(big):
    source = 'int func_60(int x) { /* 60 */ char *s = "60"; return x + 60U; }'
    a, b = {"target": code(60, big), "relocs": "[]"}, {"target": code(61, big), "relocs": "[]"}
    assert sm.literal_variants(source, a, b, big) == [source.replace('x + 60U', 'x + 61U')]
    assert sm.literal_variants('return 60 + 60;', a, b, big) == []
    assert sm.literal_variants('return -60;', a, b, big) == []
    a['relocs'] = b['relocs'] = '[[0,5,2147483648]]'
    assert sm.literal_variants(source, a, b, big) == []


def test_changed_registers_or_instructions_do_not_propose_literal_edits():
    a = {"target": code(60), "relocs": "[]"}
    b = {"target": (0x24a2003d).to_bytes(4, 'big') + code(61)[4:], "relocs": "[]"}
    assert sm.literal_variants('return x + 60;', a, b, True) == []


@pytest.fixture
def pair(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    cfg = {'platform': 'n64', 'compiler': {'toolchain': 'gcc-2.7.2-kmc', 'flags': ['-O2']},
           'big_endian': True, 'rom': {'sha1': 'test'}}
    with net.store.tx() as db:
        for pid, slug in [(1, 'source'), (2, 'destination')]:
            db.execute('INSERT INTO projects(id,slug,name,arch,big_endian,root,config) VALUES(?,?,?,?,?,?,?)',
                       (pid, slug, slug, 'mips', 1, str(tmp_path / slug), json.dumps(cfg)))
            db.execute('INSERT INTO context_versions(project_id,version,header,unit) VALUES(?,?,?,?)',
                       (pid, 1, '', ''))
            db.execute('INSERT INTO functions(id,project_id,name,size,n_insns,target,relocs,vram,unit,status,matched_source) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (pid, pid, 'old' if pid == 1 else 'new', 32, 8, code(60 if pid == 1 else 61), '[]',
                        0x80001000, '', 'matched' if pid == 1 else 'open',
                        'int old(int x) { return x + 60; }' if pid == 1 else None))
    monkeypatch.setattr(net, 'is_rom', lambda p: True)
    monkeypatch.setattr(net, 'fork_slug', lambda s: None)
    return net


def test_similar_candidates_are_judged_and_recorded_and_misses_are_cached(pair, monkeypatch):
    seen = []
    def attempt(agent, proj, a, b, source, **kwargs):
        seen.append(source)
        assert kwargs['similar'] is True
        return 'miss', 'score 20'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    job = Job()
    assert sm.run(pair.twins, 'destination', job) == 0
    assert len(seen) == 2 and 'new(int' in seen[0] and '+ 61' in seen[1]
    assert pair._function('new', 'destination')['status'] == 'open'
    assert pair.twins.cross_game_results('destination')['counts'] == {'miss': 2}
    assert sm.run(pair.twins, 'destination', Job()) == 0
    assert len(seen) == 2


def test_only_judge_acceptance_counts_and_stops_further_candidates(pair, monkeypatch):
    def attempt(agent, proj, a, b, source, **kwargs):
        if '+ 61' not in source:
            return 'miss', 'score 20'
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',matched_source=? WHERE id=?", (source, b['id']))
        return 'match', ''
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    job = Job()
    assert sm.run(pair.twins, 'destination', job) == 1
    assert job.cross_matched == 1 and job.cross_tried == 2
    assert sm.run(pair.twins, 'destination', Job()) == 0


@pytest.mark.parametrize('blocked', ['claimed', 'compiler', 'relocations', 'cancelled', 'budget', 'time', 'fork'])
def test_no_attempt_when_incompatible_busy_cancelled_or_over_budget(pair, monkeypatch, blocked):
    job = Job()
    if blocked == 'claimed':
        agent, _ = pair.local_agent('worker')
        with pair.store.tx() as db:
            db.execute('INSERT INTO claims VALUES(?,?,?,?)', (2, agent.id, 0, 9999999999))
    elif blocked == 'compiler':
        p = pair._project('destination'); cfg = json.loads(p['config']); cfg['compiler']['flags'] = ['-O0']
        with pair.store.tx() as db:
            db.execute('UPDATE projects SET config=? WHERE id=2', (json.dumps(cfg),))
    elif blocked == 'relocations':
        with pair.store.tx() as db:
            db.execute("UPDATE functions SET relocs='[[0,5,2147487744]]' WHERE id=2")
    elif blocked == 'cancelled':
        job.stop.set()
    elif blocked == 'budget':
        monkeypatch.setattr(sm, 'MAX_ATTEMPTS', 0)
    elif blocked == 'time':
        monkeypatch.setattr(sm, 'MAX_SECONDS', 0)
    elif blocked == 'fork':
        monkeypatch.setattr(pair, 'fork_slug', lambda s: s)
    def forbidden(*args, **kwargs):
        pytest.fail('must not compile this candidate')
    monkeypatch.setattr(pair.twins, '_attempt_twin', forbidden)
    assert sm.run(pair.twins, 'destination', job) == 0


def test_a_regional_constant_change_matches_with_the_real_compiler_and_link_judge(built, tmp_path, monkeypatch):  # noqa: F811
    net = RomNetwork(tmp_path / 'decomp.db', tmp_path)
    monkeypatch.setattr(net.twins, 'after_match', lambda *args: None)
    slug = net.import_rom(ROM, confirmed=True, log=lambda *_: None)
    agent, _ = net.local_agent('tester')
    assert net.compile_and_diff(agent, 'func_80000808', DAMAGE, slug).startswith('✅ MATCH')
    pid = copy_project(net, slug, 'another-region')
    fn = net._function('func_80000808', 'another-region')
    target = bytearray(fn['target'])
    offsets = [i for i in range(0, len(target), 4)
               if int.from_bytes(target[i:i+4], 'big') & 0xffff == 60
               and int.from_bytes(target[i:i+4], 'big') >> 26 == 9]
    assert offsets
    for offset in offsets:  # the compiler duplicated the assignment across two branches
        target[offset + 3] = 61
    with net.store.tx() as db:
        db.execute('UPDATE functions SET target=? WHERE id=?', (bytes(target), fn['id']))
        db.execute("UPDATE functions SET status='asm' WHERE project_id=? AND id!=?", (pid, fn['id']))
    assert net.twins.across_games('another-region') == 0
    assert sm.run(net.twins, 'another-region', Job()) == 1
    result = net._function(fn['name'], 'another-region')
    assert result['status'] == 'matched' and '= 61;' in result['matched_source']
    assert 'adapted from similar verified C' in result['matched_source']


def test_budget_leaves_work_for_next_pass_and_cancellation_stops_between_attempts(pair, monkeypatch):
    seen = []
    job = Job()
    def attempt(agent, proj, a, b, source, **kwargs):
        seen.append(source)
        job.stop.set()
        return 'miss', 'score 20'
    monkeypatch.setattr(pair.twins, '_attempt_twin', attempt)
    assert sm.run(pair.twins, 'destination', job) == 0
    assert len(seen) == 1
    job = Job()
    monkeypatch.setattr(sm, 'MAX_ATTEMPTS', 1)
    assert sm.run(pair.twins, 'destination', job) == 0
    assert len(seen) == 2 and '+ 61' in seen[-1]


def test_full_pass_runs_similarity_after_exact_transfers(pair, monkeypatch):
    order = []
    monkeypatch.setattr(pair.twins, 'set_aside_asm', lambda p: 0)
    monkeypatch.setattr(pair.twins, 'update_boosts', lambda *a, **k: None)
    monkeypatch.setattr(pair.twins, 'run', lambda *a: order.append('local'))
    monkeypatch.setattr(pair.twins, 'across_games', lambda *a: order.append('exact') or 0)
    def similar(twins, slug, job):
        assert job.state == 'running'
        order.append('similar')
        return 2
    monkeypatch.setattr(sm, 'run', similar)
    result = pair.twins.full_pass('destination')
    assert order == ['local', 'exact', 'similar'] and result.matched == 2 and result.state == 'done'
