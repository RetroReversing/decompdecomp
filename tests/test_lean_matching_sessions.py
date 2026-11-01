import json
import sys
from types import SimpleNamespace

import pytest

from decompnet.app import agent_tokens, compiler_experiments, repair_first, session_budget, tools
from decompnet.network import NetworkError
from tests.test_claims import _game


def test_trial_retrieval_drops_large_config_but_keeps_internal_reproduction():
    config={'range_checks':{'history':'x'*450000}}
    record={'agent':1,'slug':'g','function':'f','source':'int f(){return 1;}',
            'compiler':'exact-compiler','flags':'-O2','revision':'r','scope':'s','project_config':config,'result':'score 0'}
    net=SimpleNamespace(_function=lambda *args:{'name':'f'},_compiler_trials={'t':record})
    text=compiler_experiments.retrieve(net,SimpleNamespace(id=1),'g','f','t')
    response=json.loads(text)
    assert len(text)<1000 and 'project_config' not in response
    assert response['source']==record['source'] and response['flags']=='-O2'
    assert len(response['context_id'])==20
    assert record['project_config']==config
    with pytest.raises(NetworkError):
        compiler_experiments.retrieve(net,SimpleNamespace(id=2),'g','f','t')


def test_batch_claim_keeps_queue_but_hydrates_only_first(monkeypatch):
    packet='Header\n## CLAIMED: g/first\nFIRST SOURCE\n## CLAIMED: g/second\n'+'LARGE SOURCE'*1000
    net=SimpleNamespace(store=SimpleNamespace(all=lambda *args:[(1,100),(2,100)]))
    monkeypatch.setattr(agent_tokens,'brief',lambda net,text,*args:text)
    result=agent_tokens.claim_packet(net,SimpleNamespace(id=3),'g',packet)
    assert 'FIRST SOURCE' in result and 'g/second' in result
    assert 'LARGE SOURCE' not in result and 'details on demand' in result
    assert 'No new functions' in agent_tokens.claim_packet(net,SimpleNamespace(id=3),'g',packet)


def test_default_assembly_uses_mismatch_and_paging_is_explicit(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',100,'open')]);agent,_=net.local_agent('a');fn=net._function('f','game')
    lines=['op'+str(i) for i in range(120)]
    monkeypatch.setattr(net,'_asm_text',lambda *args:'\n'.join(lines))
    net.keep_diff(agent.id,fn['id'],[('i','op80','other')])
    text=agent_tokens.assembly(net,'game','f',agent=agent)
    assert 'Focus at target line 80' in text and len(text.splitlines())<=42
    full=agent_tokens.assembly(net,'game','f',focus='page',limit=120,agent=agent)
    assert 'op0' in full and 'op119' in full
    net._last_diffs.clear()
    assert 'Focus at target line 20' in agent_tokens.assembly(net,'game','f',agent=agent)


def test_budget_waits_for_parallel_tools_and_blocks_new_calls():
    budget=session_budget.Budget(tools=1,chars=10000)
    budget.begin();budget.begin()
    budget.end('saved first')
    assert not budget.ready()
    with pytest.raises(NetworkError,match='fresh session'):
        budget.begin()
    budget.end('saved second')
    assert budget.ready() and budget.active==0
    chars=session_budget.Budget(tools=100,chars=1000)
    chars.begin();chars.end('x'*1000)
    assert chars.ready()


def test_dispatch_enforces_budget_after_completed_tool(monkeypatch):
    monkeypatch.setattr('decompnet.app.tool_policy.record',lambda *a,**kw:None)  # Isolate budget behavior from storage/policy.
    net=SimpleNamespace(resolve_function=lambda *args:'f')
    agent=SimpleNamespace(id=1)
    budget=session_budget.start(net,1,'g',{'session_tool_limit':1})
    monkeypatch.setitem(tools.HANDLERS,'project_info',lambda *args,**kwargs:'saved result')
    monkeypatch.setattr('decompnet.app.agent_local_pass.after_tool',lambda net,agent,slug,name,args,result:result)
    assert tools._dispatch(net,agent,'g','project_info',{})=='saved result'
    assert budget.ready()
    with pytest.raises(NetworkError):tools._dispatch(net,agent,'g','project_info',{})
    assert budget.calls==1


def test_repair_first_pass_is_persistent_and_build_scoped(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a')
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='int f(){return 1;}'")
    net.claim_row(agent,'game','f');_,revision=agent_tokens.candidate(net,agent,'game','f')
    args={'function':'f','revision':revision,'patches':[{'old':'return 1','new':'return 2'}]}
    seen=[]
    monkeypatch.setattr('decompnet.app.local_experiments.run',lambda *args,**kwargs:seen.append(kwargs) or 'Baseline retained.')
    result=repair_first.before_variants(net,agent,'game',args)
    assert 'were not submitted' in result and seen[0]['max_trials']==3
    assert repair_first.before_variants(net,agent,'game',args) is None and len(seen)==1
    # The persisted settings entry survives process memory loss; changed build inputs invalidate it.
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'compiler':{'toolchain':'test','flags':['-O1']}}),))
    repair_first.before_variants(net,agent,'game',args)
    assert len(seen)==2


def test_bad_or_stale_patches_never_trigger_repair_first(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a')
    net.claim_row(agent,'game','f');_,revision=agent_tokens.candidate(net,agent,'game','f')
    monkeypatch.setattr('decompnet.app.local_experiments.run',lambda *args,**kwargs:pytest.fail('no compile'))
    for args in ({'function':'f','revision':'stale','patches':[]},{'function':'f','revision':revision,'patches':[]}):
        with pytest.raises(NetworkError):repair_first.before_variants(net,agent,'game',args)


def test_api_rotation_returns_before_another_provider_request(tmp_path,monkeypatch):
    import httpx

    from decompnet.agents.api import Api
    from decompnet.app import agents
    from tests.test_agents import URL, _no_agent_settings
    from tests.test_agents import _game as agent_game
    _no_agent_settings(monkeypatch)
    net=agent_game(tmp_path)
    manager=agents.Manager(net,tmp_path,URL)
    manager.prompt=lambda s:'task'
    manager.prepare_session=lambda *a,**kw:None  # Scripted provider test has its own startup facts.
    session=manager._record('game','api','api-m-1','box',{'base_url':'http://mock/v1','model':'m','context_tokens':32768})
    agent,_=net.local_agent(session.agent_name)
    session.context_budget=session_budget.start(net,agent.id,'game',{'session_tool_limit':1})
    sent=[]
    def provider(request):
        sent.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','tool_calls':[
            {'id':'c1','type':'function','function':{'name':'project_info','arguments':'{}'}}]}}]})
    real=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:real(transport=httpx.MockTransport(provider),**kw))
    monkeypatch.setitem(tools.HANDLERS,'project_info',lambda *args,**kwargs:'compact facts')
    monkeypatch.setattr('decompnet.app.agent_local_pass.after_tool',lambda net,agent,slug,name,args,result:result)
    assert Api().run(manager,session,agent)
    assert session.rotated and len(sent)==1
    # A new session gets a fresh budget, without deleting saved claims or attempts.
    fresh=session_budget.start(net,agent.id,'game',{})
    assert fresh.calls==0 and not fresh.ready()


def test_exhausted_repair_first_pass_proceeds_to_requested_variants(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('a')
    with net.store.tx() as db:db.execute("UPDATE functions SET best_source='int f(){return 1;}'")
    net.claim_row(agent,'game','f');_,revision=agent_tokens.candidate(net,agent,'game','f')
    args={'function':'f','revision':revision,'patches':[{'old':'return 1','new':'return 2'}]}
    monkeypatch.setattr('decompnet.app.local_experiments.run',lambda *args,**kwargs:'No untried applicable local repairs.')
    assert repair_first.before_variants(net,agent,'game',args) is None
    assert net.store.one("SELECT COUNT(*) AS n FROM settings WHERE key LIKE 'repair-first:%'")['n']==1


@pytest.mark.skipif(sys.platform=='win32',reason='POSIX fake process; shared budget and API tests cover Windows')
def test_cli_rotation_does_not_stop_active_tools(tmp_path,monkeypatch):
    import io

    from decompnet import agents as kinds
    from decompnet.app import agents
    from tests.test_agents import URL, _no_agent_settings
    from tests.test_agents import _game as agent_game
    _no_agent_settings(monkeypatch)
    net=agent_game(tmp_path)
    manager=agents.Manager(net,tmp_path,URL)
    session=agents.Session('worker','claude','worker','game','worker',{})
    budget=session.context_budget=session_budget.Budget(tools=1)
    ended=[]
    def output():
        budget.begin();budget.begin();budget.end('first tool saved')
        yield 'still active'
        assert not ended
        budget.end('second tool saved')
        yield 'all tools saved'
    proc=SimpleNamespace(pid=987654,stdin=io.StringIO(),stdout=output(),stderr=[],wait=lambda:0)
    monkeypatch.setattr(agents.subprocess,'Popen',lambda *args,**kwargs:proc)
    monkeypatch.setattr(manager,'_command',lambda *args:(['fake-helper'],{},'prompt'))
    monkeypatch.setattr(manager,'_preflight_tools',lambda *args:None)
    monkeypatch.setattr(manager, "prepare_session", lambda *a, **kw: None)
    monkeypatch.setattr(manager,'_save',lambda *args:None)
    monkeypatch.setattr(agents,'_proc_start',lambda *args:None)
    monkeypatch.setattr(agents,'_close_windows_job',lambda *args:None)
    monkeypatch.setattr(agents,'_terminate',lambda p:ended.append(p.pid))
    kind=kinds.get('claude')
    monkeypatch.setattr(kind,'validate_model',lambda model:None)
    monkeypatch.setattr(kind,'parse_line',lambda line,state:[('info',line)])
    assert manager._run_cli(session,'fake-token',tmp_path)
    assert session.rotated and ended==[987654] and budget.active==0
