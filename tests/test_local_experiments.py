"""Synthetic multi-family experiments share one revision and the normal judge in both modes."""
import json

import pytest

from decompnet.app import agent_tokens, local_experiments, tools
from decompnet.network import NetworkError
from tests.test_claims import _game


@pytest.mark.parametrize('mode',['rom','fork'])
def test_applicable_families_one_snapshot_and_budget(tmp_path,monkeypatch,mode):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    source='int f(){\nint a;\nshort b;\nreturn a+b;\n}'
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':mode,'compiler':{'toolchain':'example','flags':[]}}),))
        db.execute('UPDATE functions SET best_source=?',(source,))
    net.claim_row(a,'game','f');fn=net._function('f','game');net._record(fn,a,10,source,1)
    seen=[]
    def compile(agent,function,text,slug):
        seen.append(text);net._record(fn,agent,20,text,1);return 'score 20'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    _,rev=agent_tokens.candidate(net,a,'game','f')
    args={'function':'f','revision':rev,'families':['signedness','declaration_order'],'max_trials':2}
    result=tools.call(net,a,'game','try_local_repairs',args)
    assert len(seen)==2 and '1: signedness, 2: declaration_order' in result
    assert 'starting candidate retained' in result
    assert agent_tokens.candidate(net,a,'game','f')[0]==source
    # Submitted local variants are remembered; a second call advances instead of replaying them.
    tools.call(net,a,'game','try_local_repairs',args)
    assert len(seen)==3 and len(set(seen))==3
    with pytest.raises(NetworkError,match='Candidate changed'):
        local_experiments.run(net,a,'game','f','stale',max_trials=2)
    assert len(seen)==3
    assert 'try_local_repairs' in tools.project_tools(net,'game')


@pytest.mark.parametrize('bad',[0,7,True,1.5])
def test_invalid_trial_budget_does_not_compile(tmp_path,bad):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    with pytest.raises(NetworkError,match='max_trials'):
        local_experiments.run(net,a,'game','f','unused',max_trials=bad)


def test_release_handover_compacted_and_small_edit_coached_once(tmp_path,monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    text='## CLAIMED: game/f\n### Target assembly\n```asm\n'+'nop\n'*100+'```\n'
    monkeypatch.setattr(net,'release_function',lambda *args:text)
    result=tools.call(net,a,'game','release_function',{'function':'f','notes':'done'})
    assert result.count('nop\n')==24 and 'get_assembly' in result
    net.claim_row(a,'game','f')
    source='int f(){\n/*'+'x'*500+'*/\nreturn 1;}'
    net._record(fn,a,10,source,1)
    def compile(agent,function,text,slug):
        net._record(fn,agent,10,text,1);return 'score 10'
    monkeypatch.setattr(net,'compile_and_diff',compile)
    first=tools.call(net,a,'game','compile_and_diff',{'function':'f','source':source.replace('return 1','return 2')})
    second=tools.call(net,a,'game','compile_and_diff',{'function':'f','source':source.replace('return 1','return 3')})
    assert 'Small edit:' in first and 'Small edit:' not in second
    assert len(second)<250 and 'Candidate revision' in second
