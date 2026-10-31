"""A durable deep pass visits all eligible candidates without claiming unfinished work is complete."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, deep_sweep
from decompnet.app.deep_sweep import Sweeps
from decompnet.app.engine import RomNetwork
from decompnet.app.support import Jobs
from decompnet.network import NetworkError
from tests.test_deep_permuter import Log


@pytest.fixture
def sweep(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'db.sqlite', tmp_path)
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                         ('game', 'Example', 'mips', str(tmp_path), json.dumps({'platform':'n64',
                          'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2']}}))).lastrowid
        for i, status in enumerate(['open', 'open', 'matched', 'asm']):
            db.execute('INSERT INTO functions(project_id,name,status,size,vram,best_source,best_score) VALUES(?,?,?,?,?,?,?)',
                       (pid, f'f{i}', status, 16+i, 0x80001000+i*32, f'int f{i}(void) {{return 1;}}', 50))
    obj = Sweeps(net, Jobs())
    net.deep_sweeps = obj
    monkeypatch.setattr(deep_sweep, 'POLL_SECONDS', 0)
    monkeypatch.setattr(deep_sweep.random, 'shuffle', lambda batch: None)
    calls = []
    def search(net, slug, name, minutes, variants, log):
        calls.append(name)
        with net.store.tx() as db:
            db.execute('UPDATE functions SET best_score=25,best_source=? WHERE name=?',
                       (f'int {name}(void) {{return 2;}}', name))
        return {'tested':3,'matched':False,'reason':'Time limit reached','best_score':25}
    monkeypatch.setattr(deep_permuter, 'run', search)
    return obj, net, calls, tmp_path


@pytest.mark.parametrize('imported', [False, True])
def test_persists_bounded_completion_and_resumes_after_restart(sweep, monkeypatch, imported):
    obj, net, calls, path = sweep
    if imported:
        monkeypatch.setattr(net, 'fork_slug', lambda _: 'game')
        net.forks = SimpleNamespace(get=lambda _: (None,SimpleNamespace(root=path,unit=lambda _:SimpleNamespace(source=None,target=None)),{}))
    result = obj.run('game', 1, 10, Log())
    assert result['functions_tested'] == 2 and calls == ['f0','f1']
    assert obj.history('game', net._function('f0','game'))['completed']
    # The pass's own improvements must not make it immediately eligible again.
    assert obj.run('game', 1, 10, Log())['functions_tested'] == 0
    reopened = RomNetwork(path/'db.sqlite', path)
    if imported:
        monkeypatch.setattr(reopened, 'fork_slug', net.fork_slug)
        reopened.forks = net.forks
    again = Sweeps(reopened, Jobs())
    assert again.run('game',1,10,Log())['functions_tested'] == 0
    with reopened.store.tx() as db:
        db.execute("UPDATE functions SET best_source='int f0(void){return 3;}' WHERE name='f0'")
    assert again.history('game',reopened._function('f0','game'))['stale']
    assert again.run('game',1,10,Log())['functions_tested'] == 1
    assert calls == ['f0','f1','f0']


def test_budget_or_context_changes_retry_completed_passes(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    obj.run('game',1,10,Log())
    assert obj.run('game',2,10,Log())['functions_tested'] == 2
    old = net._context
    monkeypatch.setattr(net,'_context',lambda *args:dict(old(*args) or {}) | {'header':'typedef int changed;'})
    assert obj.history('game',net._function('f0','game'))['stale']
    assert obj.run('game',2,10,Log())['functions_tested'] == 2


def test_cancelled_function_is_not_complete_and_can_resume(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    def cancel(*args):
        raise RuntimeError('cancelled')
    original = deep_permuter.run
    monkeypatch.setattr(deep_permuter,'run',cancel)
    with pytest.raises(RuntimeError,match='cancelled'):
        obj.run('game',1,10,Log())
    assert not obj.history('game',net._function('f0','game'))['completed']
    monkeypatch.setattr(deep_permuter,'run',original)
    assert obj.run('game',1,10,Log())['functions_tested'] == 2


def test_busy_and_unusable_candidates_wait_without_completion(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    import time
    with net.store.tx() as db:
        db.execute("INSERT INTO claims(function_id,agent_id,expires_at) VALUES(?,?,?)",(net._function('f0','game')['id'],1,time.time()+60))
        db.execute("UPDATE functions SET best_source=NULL,best_score=NULL WHERE name='f1'")
    log=Log()
    def pause(seconds):
        log.cancelled=True
    monkeypatch.setattr(deep_sweep,'POLL_SECONDS',1)
    # Replacing time.sleep globally also intercepts Windows subprocess polling during
    # input fingerprinting, cancelling before the queue reaches its own waiting state.
    monkeypatch.setattr(deep_sweep, 'time', SimpleNamespace(time=time.time, sleep=pause))
    with pytest.raises(RuntimeError,match='cancelled'):
        obj.run('game',1,10,log)
    assert calls == []
    assert net.store.one('SELECT COUNT(*) n FROM deep_permutation_passes')['n'] == 0
    assert log.updates[-1]['waiting'] == 2


def test_bad_candidates_are_errors_not_completed_and_changed_inputs_retry(sweep, monkeypatch):
    obj,net,calls,_=sweep
    def bad(*args):
        from decompnet.network import NetworkError
        raise NetworkError('Saved C no longer compiles')
    monkeypatch.setattr(deep_permuter,'run',bad)
    assert obj.run('game',1,10,Log())['functions_tested'] == 0
    row=obj.history('game',net._function('f0','game'))
    assert row['state']=='error' and not row['completed'] and row['completed_at'] is None
    assert obj.status('game')['errors']==2


def test_changed_build_during_search_does_not_finish_the_new_build(sweep, monkeypatch):
    obj,net,calls,_=sweep
    original=deep_permuter.run
    def change(*args):
        result=original(*args)
        # Cancel before another turn; only inspect the completed first invocation.
        with net.store.tx() as db:
            db.execute("UPDATE projects SET config=?",(json.dumps({'platform':'n64','compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O3']}}),))
        return result
    monkeypatch.setattr(deep_permuter,'run',change)
    log=Log()
    original_record=obj.record
    def record(fn,scope,state,*args,**kwargs):
        original_record(fn,scope,state,*args,**kwargs)
        if state=='pending':log.cancelled=True
    monkeypatch.setattr(obj,'record',record)
    with pytest.raises(RuntimeError,match='cancelled'):obj.run('game',1,10,log)
    assert not obj.history('game',net._function('f0','game'))['completed']


def test_start_stop_and_restart_require_manual_resume_and_prevent_parallel_sweeps(sweep, monkeypatch):
    import threading
    import time

    from decompnet.network import NetworkError
    obj,net,calls,_=sweep
    entered,finish=threading.Event(),threading.Event()
    def block(*args):
        entered.set()
        finish.wait(2)
        args[-1].check_cancel()
        return {'tested':0,'matched':False}
    monkeypatch.setattr(deep_permuter,'run',block)
    first=obj.start('game',1,10)
    assert entered.wait(2)
    with pytest.raises(NetworkError,match='already running'):obj.start('game',1,10)
    obj.shutdown()
    assert obj.config('game')['enabled']  # startup clears this without re-running work
    finish.set()
    for _ in range(100):
        if obj.jobs.jobs[first['job']]['state']!='running':break
        time.sleep(.01)
    assert obj.jobs.jobs[first['job']]['state']=='cancelled'
    assert not obj.history('game',net._function('f0','game'))['completed']
    starts=[]
    restarted=Sweeps(net,Jobs())
    monkeypatch.setattr(restarted,'start',lambda *args:starts.append(args))
    restarted.resume()
    assert starts == []
    assert not restarted.config('game')['enabled']
    obj.stop('game')
    assert not obj.config('game')['enabled']


def test_claim_race_is_retried_even_if_other_worker_already_released_it(sweep, monkeypatch):
    from decompnet.network import NetworkError
    obj,net,calls,_=sweep
    original=deep_permuter.run
    first=[True]
    def race(*args):
        if first.pop() if first else False:
            raise NetworkError('This function was claimed meanwhile; try again after it is released.')
        return original(*args)
    monkeypatch.setattr(deep_permuter,'run',race)
    assert obj.run('game',1,10,Log())['functions_tested']==2
    assert obj.history('game',net._function('f0','game'))['completed']


def test_single_function_search_also_records_a_completed_pass(monkeypatch):
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import setup
    net=setup(monkeypatch)
    obj=Sweeps(net,Jobs());net.deep_sweeps=obj
    monkeypatch.setattr(Permuter,'_score',lambda *args:40)
    result=deep_permuter.run(net,'game','f',1,1,Log())
    assert result['reason']=='Variant limit reached'
    assert obj.history('game',net.function())['completed']


def test_improvement_totals_scores_and_percentages_survive_restart_and_repeat(sweep):
    obj, net, _, path = sweep
    with net.store.tx() as db:
        db.execute('UPDATE functions SET n_insns=10')
    obj.run('game', 1, 10, Log())
    status = obj.status('game')
    assert status['improved'] == 2
    row = next(r for r in status['improvements'] if r['function'] == 'f0')
    assert (row['starting_score'], row['new_score']) == (50, 25)
    assert (row['starting_percent_text'], row['new_percent_text']) == ('95.0%', '97.5%')
    fresh = net._function('f0', 'game')
    obj.improvement(fresh, 0)
    # Later poorer results, or scores from unrelated agent work, cannot rewrite these results.
    obj.improvement(fresh, 10)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_score=0,status='matched' WHERE name='f1'")
        db.execute('DELETE FROM deep_permutation_passes')  # repeat clears completion, not improvements
    reopened = Sweeps(RomNetwork(path / 'db.sqlite', path), Jobs())
    status = reopened.status('game')
    assert status['improved'] == 2
    row = next(r for r in status['improvements'] if r['function'] == 'f0')
    assert (row['starting_score'], row['new_score'], row['new_percent_text']) == (50, 0, '100%')
    row = next(r for r in status['improvements'] if r['function'] == 'f1')
    assert row['new_score'] == 25


def test_cancelled_search_keeps_partial_improvements_and_does_not_round_to_full_match(sweep, monkeypatch):
    obj, net, _, _ = sweep
    with net.store.tx() as db:
        db.execute('UPDATE functions SET n_insns=100')
    def cancel(net, slug, name, minutes, variants, log):
        obj.improvement(net._function(name, slug), 1)
        raise RuntimeError('cancelled')
    monkeypatch.setattr(deep_permuter, 'run', cancel)
    with pytest.raises(RuntimeError, match='cancelled'):
        obj.run('game', 1, 10, Log())
    row = obj.status('game')['improvements'][0]
    assert row['new_percent_text'] == '99.9%'
    assert not obj.history('game', net._function('f0', 'game'))['completed']


def test_single_search_publishes_improvements_before_completion(monkeypatch):
    from decompnet.app.permute import Permuter
    from tests.test_deep_permuter import setup
    net = setup(monkeypatch)
    obj = Sweeps(net, Jobs())
    net.deep_sweeps = obj
    monkeypatch.setattr(Permuter, '_score', lambda *args: 20)
    log = Log()
    original = log.progress
    observed = []
    def progress(value):
        observed.append(obj.status('game')['improved'])
        original(value)
    log.progress = progress
    deep_permuter.run(net, 'game', 'f', 1, 1, log)
    assert 1 in observed
    row = obj.status('game')['improvements'][0]
    assert (row['starting_score'], row['new_score']) == (40, 20)


def test_queue_counts_distinguish_current_completions_stale_sources_and_unscored(sweep):
    obj, net, _, _ = sweep
    obj.save('game', {'enabled': False, 'minutes': 1, 'max_variants': 10})
    initial = obj.status('game')
    assert (initial['queue_done'], initial['queue_left'], initial['awaiting_source']) == (0, 2, 0)
    obj.run('game', 1, 10, Log())
    complete = obj.status('game')
    assert (complete['queue_done'], complete['queue_left']) == (2, 0)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET best_source='changed' WHERE name='f0'")
        db.execute("UPDATE functions SET best_source=NULL,best_score=NULL WHERE name='f1'")
    obj.queue_cache.clear()  # Cache expires shortly after an external edit.
    changed = obj.status('game')
    assert (changed['queue_done'], changed['queue_left'], changed['awaiting_source']) == (0, 1, 1)


def test_functions_are_searched_on_several_threads_at_once(sweep, monkeypatch):
    """Two threads: each search waits for the other's (a barrier one thread alone could never pass)."""
    import threading

    obj, net, calls, _ = sweep
    meeting = threading.Barrier(2, timeout=10)
    names = {}

    def search(net, slug, name, minutes, variants, log):
        names[name] = threading.current_thread().name
        meeting.wait()
        return {'tested': 3, 'matched': False, 'reason': 'Time limit reached', 'best_score': 25}

    monkeypatch.setattr(deep_permuter, 'run', search)
    result = obj.run('game', 1, 10, Log(), 2)
    assert result['functions_tested'] == 2 and result['tested'] == 6
    assert set(names) == {'f0', 'f1'} and len(set(names.values())) == 2
    assert all(n.startswith('deep-pass') for n in names.values())
    assert all(obj.history('game', net._function(n, 'game'))['completed'] for n in ('f0', 'f1'))


def test_one_thread_searches_serially_and_zero_means_what_the_computer_can_spare(sweep, monkeypatch):
    from decompnet.app import workers

    obj, net, calls, _ = sweep
    obj.run('game', 1, 10, Log(), 1)
    assert calls == ['f0', 'f1']
    sizes = []
    real = deep_sweep.ThreadPoolExecutor
    monkeypatch.setattr(deep_sweep, 'ThreadPoolExecutor', lambda n, **kw: sizes.append(n) or real(n, **kw))
    obj.run('game', 2, 10, Log(), 0)
    obj.run('game', 3, 10, Log(), 5000)  # (no limit)
    assert sizes == [workers.default_workers(), 5000]


def test_the_thread_count_is_saved_validated_and_shown(sweep, monkeypatch):
    from decompnet.app import workers
    from decompnet.network import NetworkError

    obj, net, calls, _ = sweep
    monkeypatch.setattr(obj.jobs, 'start', lambda *args, **kw: 'job')
    for bad in (-1, 1.5, True, '4'):
        with pytest.raises(NetworkError, match='threads'):
            obj.start('game', 1, 10, False, bad)
    status = obj.start('game', 1, 10, False, 40)
    assert status['threads'] == 40 and obj.config('game')['threads'] == 40
    assert status['threads_default'] == workers.default_workers()
    assert obj.config('game') | {'threads': 0} == obj.config('game') | {'threads': 0}
    obj.start('game', 1, 10)
    assert obj.config('game')['threads'] == 0


def test_reset_metrics_preserves_progress_passes_and_evidence(sweep):
    obj, net, _, path = sweep
    obj.run('game', 1, 10, Log())
    fn = net._function('f0', 'game')
    obj.record_recipes(fn['id'], {'rewrite': [1, 0, 0, 1, 0, 25]},
                       [('rewrite', 50, 25, '{}', 1234)])
    with net.store.tx() as db:
        other = db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('other','Other','mips',?,'{}')", (str(path),)).lastrowid
        other_fn = db.execute("INSERT INTO functions(project_id,name) VALUES(?,'other_fn')", (other,)).lastrowid
    obj.record_recipes(other_fn, {'rewrite': [1, 0, 0, 1, 0, 25]}, [])
    obj.record_recipes(fn['id'], {}, [], {('rewrite', 'random rewrites'): [1, 0, 0, 0, 0, 1, 0, 10, 25]})
    obj.record_recipes(other_fn, {}, [], {('rewrite', 'random rewrites'): [1, 0, 0, 0, 0, 1, 0, 10, 25]})
    before = dict(net._function('f0', 'game'))
    result = obj.reset_metrics('game')
    assert result['recipes'] == result['recipe_wins'] == result['improvements'] == result['measurements'] == []
    assert obj.measurements(other)[0]['attempted'] == 1
    assert result['completed'] == 2
    assert dict(net._function('f0', 'game')) == before
    assert obj.recipes(other)[0]['tested'] == 1
    assert net.store.one('SELECT COUNT(*) n FROM deep_permutation_recipe_wins')['n'] == 1
    reopened = Sweeps(net, Jobs())
    assert reopened.recipe_wins(fn['project_id']) == []
    reopened.record_recipes(fn['id'], {'rewrite': [1, 0, 0, 1, 0, 5]},
                            [('rewrite', 25, 20, '{}', 1235)])
    assert reopened.recipes(fn['project_id'])[0]['tested'] == 1
    assert reopened.recipe_wins(fn['project_id'])[0]['created_at'] == 1235


@pytest.mark.parametrize('kind', ['deep-permuter', 'deep-permutation-pass'])
def test_reset_metrics_rejects_active_searches(sweep, kind):
    obj, net, _, _ = sweep
    obj.jobs.jobs['active'] = {'state': 'running', 'kind': kind}
    with pytest.raises(NetworkError, match='Stop active deep'):
        obj.reset_metrics('game')


@pytest.mark.parametrize('threads', [1, 2])
def test_random_queue_uses_only_eligible_functions_once(sweep, monkeypatch, threads):
    obj, net, calls, _ = sweep
    shuffled = []
    def reverse(batch):
        shuffled.append([fn['name'] for fn, scope in batch])
        batch.reverse()
    monkeypatch.setattr(deep_sweep.random, 'shuffle', reverse)
    obj.run('game', 1, 10, Log(), threads)
    assert shuffled[0] == ['f0', 'f1']
    assert sorted(calls) == ['f0', 'f1']
    if threads == 1:
        assert calls == ['f1', 'f0']
    calls.clear()
    obj.run('game', 1, 10, Log(), threads)
    assert calls == []  # Completed passes remain skipped despite random selection.


def test_restart_reshuffles_interrupted_functions(sweep, monkeypatch):
    obj, net, calls, _ = sweep
    selected = []
    def interrupted(*args):
        selected.append(args[2])
        raise RuntimeError('stopped')
    monkeypatch.setattr(deep_permuter, 'run', interrupted)
    with pytest.raises(RuntimeError, match='stopped'):
        obj.run('game', 1, 10, Log())
    monkeypatch.setattr(deep_sweep.random, 'shuffle', lambda batch: batch.reverse())
    with pytest.raises(RuntimeError, match='stopped'):
        obj.run('game', 1, 10, Log())
    assert selected == ['f0', 'f1']


def test_measurements_accumulate_by_family_and_phase_without_rewriting_old_totals(sweep):
    obj, net, _, _ = sweep
    fn = net._function('f0', 'game')
    obj.record_recipes(fn['id'], {'rewrite': [5, 1, 0, 1, 0, 10]}, [],
                       {('rewrite', 'random rewrites'): [3, 1, 0, 1, 0, 1, 0, 100, 10]})
    obj.record_recipes(fn['id'], {'rewrite': [2, 0, 0, 1, 1, 5]}, [],
                       {('rewrite', 'random rewrites'): [1, 0, 1, 0, 0, 0, 0, 50, 0],
                        ('rewrite', 'local repairs after random improvement'): [1, 0, 0, 0, 0, 1, 1, 20, 5]})
    rows = {r['phase']: r for r in obj.measurements(fn['project_id'])}
    random = rows['random rewrites']
    assert (random['attempted'], random['compile_failed'], random['unscored'], random['unchanged'],
            random['improved'], random['elapsed_ms'], random['score_gain']) == (4, 1, 1, 1, 1, 150, 10)
    assert rows['local repairs after random improvement']['matched'] == 1
    assert obj.recipes(fn['project_id'])[0]['tested'] == 7


def test_rotated_functions_remain_eligible_next_sweep_without_looping_in_this_one(sweep,monkeypatch):
    obj,net,calls,_=sweep
    def rotate(net,slug,name,minutes,variants,log):
        assert log.adaptive_budget
        calls.append(name)
        return {'tested':32,'matched':False,'reason':'Budget rotation: repeated function output without improvement'}
    monkeypatch.setattr(deep_permuter,'run',rotate)
    assert obj.run('game',1,100,Log())['functions_tested']==2
    assert calls==['f0','f1']
    assert net.store.one('SELECT state FROM deep_permutation_passes LIMIT 1')['state']=='rotated'
    assert obj.run('game',1,100,Log())['functions_tested']==2
    assert calls==['f0','f1','f0','f1']
    assert net._function('f0','game')['status']=='open'
