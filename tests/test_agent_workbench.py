"""Scheduler evidence, reported resource caps and model accounting without any provider calls."""
import json
import time

import pytest

from decompnet.app import agent_limits, agent_workbench, compiler_preflight
from decompnet.app.agents import Manager
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('DECOMP_DETECT_AGENTS','off')
    monkeypatch.setattr(agent_limits,'watch',lambda *a:None)
    monkeypatch.setattr(Manager,'_prepare',lambda *a:None)
    monkeypatch.setattr(Manager,'_run',lambda *a:None)
    net=RomNetwork(tmp_path/'state.db',tmp_path)
    with net.store.tx() as db:
        db.execute("INSERT INTO projects(slug,name,arch,root,config) VALUES('game','Game','mips',?,?)",(str(tmp_path),json.dumps({'kind':'rom'})))
        for name,size in [('one',40),('two',80),('three',120)]:
            db.execute('INSERT INTO functions(project_id,name,n_insns,size,best_source) VALUES(1,?, ?, ?,?)',(name,size//4,size,'int f(void){return 0;}'))
    manager=Manager(net,tmp_path/'helpers','http://127.0.0.1:1/mcp')
    sid=manager.start('codex','game',options={'model':'known-model','selection':{'strategy':'hardest'}})[0]
    s=manager.sessions[sid]
    agent,_=net.local_agent(s.agent_name)
    return net,manager,s,agent


def test_preview_matches_real_queue_and_never_writes_claims(setup):
    net,manager,s,agent=setup
    before=net.store.one('SELECT COUNT(*) AS n FROM events')['n']
    preview=agent_workbench.preview(manager,s.id)
    assert [r['name'] for r in preview['next']]==['three','two','one']
    assert net.store.one('SELECT COUNT(*) AS n FROM claims')['n']==0
    assert net.store.one('SELECT COUNT(*) AS n FROM events')['n']==before
    assert net.claim_row(agent,'game')['name']==preview['next'][0]['name']
    other,_=net.local_agent('other')
    net.claim_row(other,'game',function='one')
    preview=agent_workbench.preview(manager,s.id)
    assert preview['current']=='three'
    assert [r['name'] for r in preview['next']]==['two']
    assert preview['excluded_counts']['currently claimed']==2


@pytest.mark.parametrize('caps',[{'tokens':-1},{'usd':'nan'},{'seconds':1.1},{'tokens':True},{'nonsense':1},[]])
def test_limits_validate(caps):
    with pytest.raises(NetworkError): agent_limits.validate(caps)


def test_limits_stop_active_worker_and_ignore_metric_resets(setup):
    net,manager,s,agent=setup
    manager.set_controls(s.id,{'tokens':100,'seconds':30})
    s.limit_origin={'at':100,'tokens':1000,'usd':5}
    s.tokens=1099
    s.usd=5
    assert agent_limits.reason(manager,s,110) is None
    s.options['_metrics']={'tokens':1099,'at':110}
    assert agent_limits.reason(manager,s,110) is None
    s.tokens=1100
    assert 'tokens' in agent_limits.reason(manager,s,110)
    s.limit_origin['at']=time.time()
    assert agent_limits.enforce(manager,s)
    assert s.state=='stopped' and s.stop.is_set() and 'tokens' in s.why
    assert not agent_limits.enforce(manager,s)


def test_idle_limit_credits_actual_improvement_not_a_tie(setup):
    net,manager,s,agent=setup
    s.options['limits']={'idle_seconds':10}
    s.limit_origin={'at':100,'tokens':0,'usd':0}
    with net.store.tx() as db:
        for score,stamp in [(100,102),(100,109),(200,110)]:
            db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,?,?,1,?)',(agent.id,score,stamp))
    assert agent_limits.reason(manager,s,111) is None
    assert 'no-improvement' in agent_limits.reason(manager,s,112)
    with net.store.tx() as db:
        db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,?,50,1,111)',(agent.id,))
    assert agent_limits.reason(manager,s,112) is None


def test_controls_are_saved_and_reported_dollars_are_not_estimates(setup):
    net,manager,s,agent=setup
    manager.set_controls(s.id,{'usd':1.5})
    s.limit_origin={'at':100,'usd':0,'tokens':0}
    s.tokens=10**9
    assert agent_limits.reason(manager,s,101) is None
    s.usd=1.5
    assert 'usd' in agent_limits.reason(manager,s,101)
    assert json.loads(net.store.one('SELECT options FROM helpers WHERE id=?',s.id)['options'])['limits']=={'usd':1.5}


def test_model_evidence_has_function_scores_but_only_project_costs(setup):
    net,manager,s,agent=setup
    row=manager._session_begin(s)
    now=time.time()
    with net.store.tx() as db:
        for score in (500,100,100):
            db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,?,?,1,?)',(agent.id,score,now))
    evidence=agent_workbench.models(manager,'game','one')
    model=evidence['models'][0]
    assert model['model']=='known-model' and model['checks']==3 and model['improvements']==2
    assert model['best_percent']==90 and model['project_usd'] is None
    assert 'not costs allocated to this function' in evidence['note']
    manager._session_end(s,row,agent,0,'test')


def test_diagnosis_uses_observed_markers_not_an_ai_or_cause_claim():
    d=agent_workbench.diagnosis({'rows':[['r','a','b'],['r','b','c'],['s','a','c'],[' ','a','a']]})
    assert d['groups'][0]['count']==2 and d['groups'][0]['rows']==[0,1]
    assert d['groups'][1]['title']=='Stack layout' and 'not a proven cause' in d['note']
    assert agent_workbench.diagnosis({'error':'No object'})['error']=='No object'


def test_compiler_preflight_uses_current_cache_without_provider_calls(setup,monkeypatch):
    net,manager,s,agent=setup
    calls=[]
    monkeypatch.setattr(net,'compiler_test_result',lambda *a:{'stale':False})
    monkeypatch.setattr(net,'run_compiler_comparison',lambda slug,**kw:calls.append((slug,kw)))
    compiler_preflight.run(manager,s)
    assert not calls
    monkeypatch.setattr(net,'compiler_test_result',lambda *a:{'stale':True})
    compiler_preflight.run(manager,s)
    assert calls[0][0]=='game' and calls[0][1]['function']=='three'
    s.stop.set()
    with pytest.raises(NetworkError): calls[0][1]['log'].check_cancel()


def test_byte_efficiency_counts_unique_verified_bytes_and_handles_unknown_usage(setup):
    net,manager,s,agent=setup
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='matched',matched_agent_id=? WHERE id IN (1,2)",(agent.id,))
    s.tokens=1000000
    row=manager.snapshot()[0]
    assert row['verified_bytes']==120 and row['bytes_per_million_tokens']==120
    s.tokens=0
    assert manager.snapshot()[0]['bytes_per_million_tokens'] is None


def test_preview_honors_starred_priority_in_automatic_mode(setup):
    net,manager,s,agent=setup
    net.set_selection('game',s.agent_name,{})
    net._update_cfg('game',lambda cfg:cfg.update(priority=['two']))
    q=agent_workbench.preview(manager,s.id)
    assert q['next'][0]['name']=='two'
    assert net.claim_row(agent,'game')['name']=='two'


@pytest.mark.parametrize('kind',['rom','fork'])
def test_compiler_unchecked_strategy_is_a_shared_queue_order(setup,kind):
    net,manager,s,agent=setup
    net._update_cfg('game',lambda cfg:cfg.update(kind=kind))
    net.set_selection('game',s.agent_name,{'strategy':'compiler_unchecked'})
    with net.store.tx() as db:
        db.execute("INSERT INTO compiler_tests VALUES(1,'digest','{}',1)")
    assert net.claim_row(agent,'game')['name']=='two'
    if kind=='fork':
        with pytest.raises(NetworkError,match='ROM projects only'):
            manager.set_controls(s.id,{},True)
