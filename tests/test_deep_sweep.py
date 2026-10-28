"""A durable deep pass visits all eligible candidates without claiming unfinished work is complete."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import deep_permuter, deep_sweep
from decompnet.app.deep_sweep import Sweeps
from decompnet.app.engine import RomNetwork
from decompnet.app.support import Jobs
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
    monkeypatch.setattr(deep_sweep.time,'sleep',pause)
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


def test_start_stop_and_restart_keep_enabled_intent_and_prevent_parallel_sweeps(sweep, monkeypatch):
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
    assert obj.config('game')['enabled']  # normal app exit resumes next launch
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
    assert starts==[('game',1,10)]
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
