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
    assert len(calls) == 1 + 8 + 24 * (search.DEPTH - 1)
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
    free_candidates.budget(job, attempts=1)
    search.run(pair.twins, 'destination', job)
    assert calls == ['seed'] and job.reuse_attempts == 1
    job = Job()
    free_candidates.budget(job, attempts=1)
    search.run(pair.twins, 'destination', job)
    assert calls == ['seed', 'seedx']
    with pair.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(2,1,0,99999999999)')
    search.run(pair.twins, 'destination', Job())
    assert calls == ['seed', 'seedx']


def test_rotation_survives_new_jobs(pair, monkeypatch):
    from decompnet.app import address_repairs, free_candidates, similar_matches
    pair.cascades.iterative_threads = 1  # (the order the functions are visited in is the thing tested)
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET best_source='seed',best_score=20,n_insns=8 WHERE id=2")
        db.execute("INSERT INTO functions(id,project_id,name,target,relocs,status,vram,size,n_insns,best_source,best_score) "
                   "SELECT 3,project_id,'later',target,relocs,status,vram+32,size,n_insns,best_source,best_score FROM functions WHERE id=2")
    monkeypatch.setattr(address_repairs, 'variants', lambda s,*a: [s+'x'])
    monkeypatch.setattr(similar_matches, 'repair_variants', lambda *a: [])
    visited = []
    monkeypatch.setattr(pair, 'try_quietly', lambda a,p,f,s: visited.append(f['id']) or dict(matched=False,score=20))
    for _ in range(3):
        job = Job()
        free_candidates.budget(job, attempts=1)
        search.run(pair.twins, 'destination', job)
    assert visited == [2, 3, 2]


def test_optional_phase_disabled_and_independent_of_exhausted_normal_budget(pair, monkeypatch):
    from decompnet.app import free_candidates
    parent = Job()
    free_candidates.budget(parent, attempts=1)
    parent.reuse_attempts = 1
    called = []
    def run(twins, slug, job):
        assert job.stop is parent.stop
        assert job.reuse_attempts == 0 and job.reuse_limit == 7
        assert job.reuse_deadline > __import__('time').monotonic()
        job.reuse_attempts = 2
        called.append(job)
        return 0
    monkeypatch.setattr(search, 'run', run)
    assert search.optional_phase(pair.twins, 'destination', parent) == 0
    assert not called
    pair.cascades.configure(False, iterative_enabled=True, iterative_attempts=7)
    search.optional_phase(pair.twins, 'destination', parent)
    assert len(called) == 1 and parent.reuse_attempts == 1
    assert pair.store.one('SELECT attempts FROM iterative_runs')['attempts'] == 2
    parent.stop.set()
    search.optional_phase(pair.twins, 'destination', parent)
    assert len(called) == 1


def test_output_duplicates_bound_expansion_but_preserve_two_source_shapes():
    state, expanded = seed(), []
    def evaluate(source):
        return dict(score=20, matched=False, candidate_target='001122', candidate_relocs='[]')
    def variants(source, report):
        expanded.append(source)
        return ['a','b','c'] if source=='seed' else []
    assert not search.advance(state,evaluate,variants,lambda:False)
    assert expanded==['seed','a','b']
    assert len(state['outputs'])==2
    assert max(state['outputs'].values())==2
    assert search.output_key({'rows':[]}) is None
    assert search.output_key({'candidate_target':'00','candidate_relocs':'[]'}) != search.output_key(
        {'candidate_target':'00','candidate_relocs':'[1]'})


def test_targeted_repairs_chain_and_keep_serialized_progress(monkeypatch):
    from decompnet.app import draft_repairs
    def proposals(source,name,arch,asm,flags,labels,header='',limit=None,tables=(),callees=''):
        out={'seed':['lifetime'], 'lifetime':['done']}.get(source,[])
        labels.update({p:'split_scalar_lifetime' for p in out})
        return out
    monkeypatch.setattr(draft_repairs,'variants',proposals)
    state=seed();calls=[]
    def judge(source):
        calls.append(source)
        return dict(score={'seed':20,'lifetime':10,'done':0}[source],matched=source=='done')
    variants=lambda s,r:search.targeted_variants(s,r,{'name':'f','asm':''},'mips',[])
    assert not search.advance(state,judge,variants,lambda:len(calls)>=2)
    state=json.loads(json.dumps(state))
    assert search.advance(state,judge,variants,lambda:False)
    assert calls==['seed','lifetime','done']


def test_targeted_ranking_prefers_load_order_for_scheduling_diffs(monkeypatch):
    from decompnet.app import draft_repairs
    def proposals(source,name,arch,asm,flags,labels,header='',limit=None,tables=(),callees=''):
        labels.update(a='signedness',b='pointer_before_store',c='paired_load_order')
        return ['a','b','c']
    monkeypatch.setattr(draft_repairs,'variants',proposals)
    assert search.targeted_variants('seed',{'rows':[('o','x','y')]},{'name':'f','asm':''},'mips',[])==['c','b','a']


def test_new_repair_rules_reopen_frontier_and_reuse_cached_seed(pair,monkeypatch):
    from decompnet.app import address_repairs, similar_matches
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET best_source='seed',best_score=20,n_insns=8 WHERE id=2")
    monkeypatch.setattr(address_repairs,'variants',lambda *a:[])
    monkeypatch.setattr(similar_matches,'repair_variants',lambda *a:[])
    monkeypatch.setattr(search,'targeted_variants',lambda *a:[])
    monkeypatch.setattr(search,'repair_rules',lambda:['old'])
    calls=[]
    monkeypatch.setattr(pair,'try_quietly',lambda a,p,f,s:calls.append(s) or dict(score=20,matched=False))
    search.run(pair.twins,'destination',Job())
    monkeypatch.setattr(search,'repair_rules',lambda:['new'])
    monkeypatch.setattr(search,'targeted_variants',lambda s,*a:['new-shape'] if s=='seed' else [])
    search.run(pair.twins,'destination',Job())
    assert calls==['seed','new-shape']


def two_open_functions(pair, monkeypatch):
    from decompnet.app import address_repairs, similar_matches
    with pair.store.tx() as db:
        db.execute("UPDATE functions SET best_source='seed',best_score=20,n_insns=8 WHERE id=2")
        db.execute("INSERT INTO functions(id,project_id,name,target,relocs,status,vram,size,n_insns,best_source,best_score) "
                   "SELECT 3,project_id,'later',target,relocs,status,vram+32,size,n_insns,best_source,best_score FROM functions WHERE id=2")
    monkeypatch.setattr(address_repairs, 'variants', lambda s, *a: [])
    monkeypatch.setattr(similar_matches, 'repair_variants', lambda *a: [])


def test_functions_are_repaired_on_several_threads_at_once(pair, monkeypatch):
    """Two threads: each function's judge waits for the other's (a barrier that one thread alone could never pass)."""
    import threading

    two_open_functions(pair, monkeypatch)
    meeting = threading.Barrier(2, timeout=10)
    threads = set()

    def judge(agent, proj, fn, source):
        threads.add(threading.current_thread().name)
        meeting.wait()
        return dict(matched=False, score=20)

    monkeypatch.setattr(pair, 'try_quietly', judge)
    pair.cascades.iterative_threads = 2
    job = Job()
    search.run(pair.twins, 'destination', job)
    assert len(threads) == 2 and all(n.startswith('iterative-repair') for n in threads)
    assert job.reuse_attempts == 2  # (the job's count, with both threads adding to it)


def test_one_thread_repairs_one_function_after_another(pair, monkeypatch):
    import threading

    two_open_functions(pair, monkeypatch)
    threads = set()
    monkeypatch.setattr(pair, 'try_quietly', lambda *a: threads.add(threading.current_thread().name) or dict(matched=False, score=20))
    pair.cascades.iterative_threads = 1
    search.run(pair.twins, 'destination', Job())
    assert len(threads) == 1


def test_the_default_is_what_the_computer_can_spare(pair, monkeypatch):
    from decompnet.app import workers

    two_open_functions(pair, monkeypatch)
    sizes = []
    real = search.ThreadPoolExecutor
    monkeypatch.setattr(search, 'ThreadPoolExecutor', lambda n, **kw: sizes.append(n) or real(n, **kw))
    monkeypatch.setattr(pair, 'try_quietly', lambda *a: dict(matched=False, score=20))
    search.run(pair.twins, 'destination', Job())
    pair.cascades.iterative_threads = 3
    search.run(pair.twins, 'destination', Job())
    assert sizes == [workers.default_workers(), 3]


def test_reserved_detour_survives_better_siblings_and_can_lead_to_exact_match():
    state, expanded = seed(), []
    reports = {'seed':20, 'one':10, 'two':11, 'three':12, 'detour':24, 'done':0}
    def variants(source, report):
        expanded.append(source)
        return {'seed':['one', 'two', 'three', 'detour'], 'detour':['done']}.get(source, [])
    assert search.advance(state, lambda s: dict(score=reports[s], matched=s == 'done'), variants, lambda: False)
    assert 'detour' in expanded and 'three' not in expanded


def test_detour_limit_does_not_compound_and_survives_serialization():
    state, calls = seed(), []
    def evaluate(source):
        calls.append(source)
        return dict(score={'seed':20, 'detour':24, 'too_far':28}[source], matched=False)
    def variants(source, report):
        return {'seed':['detour'], 'detour':['too_far']}.get(source, [])
    assert not search.advance(state, evaluate, variants, lambda: len(calls) >= 2)
    state = json.loads(json.dumps(state))
    expanded = []
    assert not search.advance(state, evaluate, lambda s,r: expanded.append(s) or [], lambda: False)
    assert calls == ['seed', 'detour', 'too_far'] and not expanded


def test_region_gain_wins_reserved_detour_slot_over_a_lower_total_score():
    from .test_region_repairs import rows
    state, expanded = seed(), []
    reports = {'seed':dict(score=20,rows=rows({0,1,2})),
               'weak':dict(score=21,rows=rows({0,1})),
               'local_fix':dict(score=24,rows=rows(set(range(20,32)))),
               'done':dict(score=0,matched=True)}
    def variants(source, report):
        expanded.append(source)
        return {'seed':['weak','local_fix'], 'local_fix':['done']}.get(source, [])
    assert search.advance(state, lambda s: reports[s], variants, lambda:False)
    assert 'local_fix' in expanded and 'weak' not in expanded


def test_unhelpful_frame_growth_is_not_expanded_but_exact_verdict_wins():
    baseline = [(' ','addiu $sp, $sp, -32','addiu $sp, $sp, -32'),
                ('r','move $s0, $a0','move $s1, $a0')]
    larger = [('s','addiu $sp, $sp, -32','addiu $sp, $sp, -40'),
              ('r','move $s0, $a0','move $s1, $a0'), ('>','','nop')]
    expanded = []
    state = seed()
    state['pending'][0]['baseline_rows'] = baseline
    assert not search.advance(state,lambda s:dict(score=21,matched=False,rows=larger),
                              lambda s,r:expanded.append(s) or [],lambda:False,arch='mips')
    assert not expanded
    state = seed()
    state['pending'][0]['baseline_rows'] = baseline
    assert search.advance(state,lambda s:dict(score=0,matched=True,rows=larger),
                          lambda s,r:[],lambda:False,arch='mips')

def test_identical_output_plateau_can_continue_at_each_depth():
    state = seed()
    calls = []
    def evaluate(source):
        calls.append(source)
        return dict(score=20, matched=source == 'done', candidate_target='aa', candidate_relocs='[]')
    def variants(source, report):
        return {'seed':['a'], 'a':['b'], 'b':['done']}[source]
    assert search.advance(state, evaluate, variants, lambda: False)
    assert calls == ['seed', 'a', 'b', 'done']
