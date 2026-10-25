"""Bounded search can chain edits and resume without accepting a heuristic score as a match."""
import json

from decompnet.app import iterative_repairs as search
from decompnet.app.twins import Job

from .test_similar_matches import pair as pair_fixture

pair = pair_fixture


def seed():
    return dict(pending=[dict(source='seed', depth=0, ceiling=20)], next=[], seen=['seed'])


def test_chains_improving_edits_and_resumes_serialized_frontier():
    state, calls = seed(), []
    def evaluate(source):
        calls.append(source)
        return dict(score={'seed':20, 'step':10, 'done':0}[source], matched=source == 'done')
    variants = lambda source, report: {'seed':['step'], 'step':['done']}[source]
    assert not search.advance(state, evaluate, variants, lambda: len(calls) >= 2)
    state = json.loads(json.dumps(state))
    assert search.advance(state, evaluate, variants, lambda: False)
    assert calls == ['seed', 'step', 'done']


def test_plateau_alternatives_but_not_worse_or_failed_builds():
    state, expanded = seed(), []
    def variants(source, report):
        expanded.append(source)
        return ['worse', 'bad', 'equal', 'zero'] if source == 'seed' else []
    reports = {'seed':20, 'worse':30, 'bad':None, 'equal':20, 'zero':0}
    assert not search.advance(state, lambda s: dict(score=reports[s], matched=False), variants, lambda: False)
    assert expanded == ['seed', 'equal']


def test_depth_and_width_bound_cycles_and_branching():
    state, calls = seed(), []
    def evaluate(source):
        calls.append(source)
        return dict(score=20, matched=False)
    assert not search.advance(state, evaluate, lambda s, r: [s+str(i) for i in range(8)] + ['seed'], lambda: False)
    assert len(calls) == 1 + 8 + 24 + 24
    assert len(calls) == len(set(calls))


def test_busy_and_stopped_preserve_pending_work():
    state = seed()
    assert not search.advance(state, lambda s: None, lambda *a: [], lambda: False)
    assert state == seed()
    assert not search.advance(state, lambda s: 1/0, lambda *a: [], lambda: True)
    assert state == seed()


def test_cached_frontier_and_context_invalidation(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET best_source='seed',best_score=20,n_insns=8 WHERE id=2")
    calls = []
    monkeypatch.setattr(pair, 'try_quietly', lambda a,p,f,s: calls.append(s) or dict(matched=False,score=20))
    from decompnet.app import address_repairs, similar_matches
    monkeypatch.setattr(address_repairs, 'variants', lambda *a: [])
    monkeypatch.setattr(similar_matches, 'repair_variants', lambda *a: [])
    assert search.run(pair.twins, 'destination', Job()) == 0
    assert search.run(pair.twins, 'destination', Job()) == 0
    assert calls == ['seed']
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET target=? WHERE id=2", (b'new target',))
    search.run(pair.twins, 'destination', Job())
    assert calls == ['seed', 'seed']
    assert pair._function('new', 'destination')['status'] == 'open'


def test_run_chains_repairs_and_counts_only_verified_match(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET best_source='seed',best_score=20,n_insns=8 WHERE id=2")
    from decompnet.app import address_repairs, similar_matches
    monkeypatch.setattr(address_repairs, 'variants', lambda s,*a: {'seed':['step'], 'step':['done']}.get(s, []))
    monkeypatch.setattr(similar_matches, 'repair_variants', lambda *a: [])
    seen = []
    def judge(a,p,f,s):
        seen.append(s)
        return dict(matched=s == 'done', score={'seed':20, 'step':10, 'done':0}[s])
    monkeypatch.setattr(pair, 'try_quietly', judge)
    job = Job()
    assert search.run(pair.twins, 'destination', job) == 1
    assert seen == ['seed', 'step', 'done']
    assert job.reuse_matched == 1 and job.reuse_bytes == 32 and job.reuse_attempts == 3


def test_wave_quota_resumes_and_claims_are_skipped(pair, monkeypatch):
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET best_source='seed',best_score=20,n_insns=8 WHERE id=2")
    from decompnet.app import address_repairs, free_candidates, similar_matches
    monkeypatch.setattr(address_repairs, 'variants', lambda s,*a: [s+'x'])
    monkeypatch.setattr(similar_matches, 'repair_variants', lambda *a: [])
    calls = []
    monkeypatch.setattr(pair, 'try_quietly', lambda a,p,f,s: calls.append(s) or dict(matched=False,score=20))
    job = Job()
    free_candidates.budget(job, attempts=4)
    search.run(pair.twins, 'destination', job)
    assert calls == ['seed'] and job.reuse_attempts == 1
    job = Job()
    free_candidates.budget(job, attempts=4)
    search.run(pair.twins, 'destination', job)
    assert calls == ['seed', 'seedx']
    with pair.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(2,1,0,99999999999)')
    search.run(pair.twins, 'destination', Job())
    assert calls == ['seed', 'seedx']
