import json

import anyio
import pytest

from decompnet import scoring
from decompnet.agents.api import FINISHING, api_tool_schemas
from decompnet.app import agent_tokens, tools
from decompnet.network import NetworkError
from tests.test_claims import _game


@pytest.mark.parametrize('mode', ['rom', 'fork'])
def test_patch_revision_uniqueness_and_normal_judge(tmp_path, monkeypatch, mode):
    net, pid = _game(tmp_path, [('f', 10, 'open')])
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?', (json.dumps({'kind': mode}),))
        db.execute('UPDATE functions SET best_source=?', ('int f(){return 1;}',))
    a, _ = net.local_agent('a')
    net.claim_row(a, 'game', 'f')
    seen = []
    def compile(agent, function, source, slug):
        seen.append(source)
        net._record(net._function('f', 'game'), agent, 10, source, 1)
        return 'score 10'
    monkeypatch.setattr(net, 'compile_and_diff', compile)
    source, revision = agent_tokens.candidate(net, a, 'game', 'f')
    text = tools.call(net, a, 'game', 'compile_patch', {'function': 'f', 'revision': revision, 'old': 'return 1', 'new': 'return 2'})
    assert seen == ['int f(){return 2;}'] and 'Candidate revision' in text
    assert agent_tokens.candidate(net, a, 'game', 'f')[0] == seen[0]
    with pytest.raises(NetworkError, match='Candidate changed'):
        agent_tokens.patch(net, a, 'game', 'f', revision, 'return 1', 'return 3')
    _, revision = agent_tokens.candidate(net, a, 'game', 'f')
    with pytest.raises(NetworkError, match='exactly once'):
        agent_tokens.patch(net, a, 'game', 'f', revision, '', 'x')
    assert len(seen) == 1
    assert 'score 10' in agent_tokens.history(net, net._function('f', 'game'))


def test_new_claim_starts_from_best_not_old_agent_failure(tmp_path):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    a, _ = net.local_agent('a')
    fn = net._function('f', 'game')
    net._record(fn, a, 90, 'old failure', 1)
    with net.store.tx() as db:
        db.execute('UPDATE functions SET best_source=?', ('new best',))
    net.claim_row(a, 'game', 'f')
    assert agent_tokens.candidate(net, a, 'game', 'f')[0] == 'new best'


def test_incremental_diff_does_not_confuse_equal_scores_with_identical_rows(tmp_path):
    net, _ = _game(tmp_path, [('f', 200, 'open')])
    a, _ = net.local_agent('a');fn=net._function('f','game')
    rows=[('i', f'load {i}', f'load {i+1}') for i in range(200)]
    net.keep_diff(a.id, fn['id'], rows)
    full='score 200\n'+scoring.render_rows(rows)+'\nwhen linking your code at the function\'s address:\nmissing datum'
    out=agent_tokens.feedback(net,a,'game','f',full,None)
    assert len(out)<len(full) and 'missing datum' in out
    assert 'Instruction diff unchanged' in agent_tokens.feedback(net,a,'game','f',full,rows)
    before=list(rows);before[0]=('i','different','different too')
    assert 'Instruction diff unchanged' not in agent_tokens.feedback(net,a,'game','f',full,before)
    page=agent_tokens.diff_page(net,a,'game','f',start=190,limit=10)
    assert 'load 199' in page and 'load 20 ' not in page
    assert net.store.one('SELECT COUNT(*) AS n FROM attempts')['n']==0


def test_repeat_restores_its_diff_and_compact_reply(tmp_path):
    net, _ = _game(tmp_path, [('f',10,'open')]);a,_=net.local_agent('a');b,_=net.local_agent('b');fn=net._function('f','game')
    rows=[('i','one','two')];net.keep_diff(a.id,fn['id'],rows)
    net.remember_compile('key',a,fn,'score 10\n'+'long diff '*200,10)
    repeat=net.repeat_of('key',b,fn)
    out=agent_tokens.feedback(net,b,'game','f',repeat['text'],None)
    assert len(out)<500 and net._last_diffs[b.id,fn['id']][0]==rows
    assert 'one' in agent_tokens.diff_page(net,b,'game','f')


def test_claim_brief_pages_assembly_and_preserves_candidate_and_history(tmp_path):
    net,_=_game(tmp_path,[('f',100,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    net._record(fn,a,30,'int f(){return 1;}',1)
    text='## CLAIMED: game/f\n```asm\n'+'\n'.join(f'line{i}' for i in range(200))+'\n```\n### Best attempt\nint f(){return 1;}'
    out=agent_tokens.brief(net,text,'game')
    assert len(out)<len(text) and 'line199' not in out and 'int f(){return 1;}' in out and 'supporting attempt history' in out


def test_new_tools_and_paging_are_exposed_to_mcp_and_api(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    mcp=tools.build_mcp(net,lambda ctx:(a,'game'))
    definitions=anyio.run(mcp.list_tools)
    for name in ('get_candidate','compile_patch','get_assembly','last_diff'):
        assert name in {t.name for t in definitions}
        assert name in {t['function']['name'] for t in api_tool_schemas()}
        assert name in tools.tool_names(False) and name in tools.tool_names(True)
    patch=next(t['function'] for t in api_tool_schemas() if t['function']['name']=='compile_patch')
    assert patch['parameters']['required']==['function','revision','old','new']
    assert 'compile_patch' in FINISHING


def test_fork_reads_disk_candidate_and_all_target_assembly(tmp_path, monkeypatch):
    from types import SimpleNamespace
    net,_=_game(tmp_path,[('f',600,'open')]);a,_=net.local_agent('a')
    fp=SimpleNamespace(unit=lambda name: name, diff=lambda *args: SimpleNamespace(target_asm=lambda:'\n'.join(f'op{i}' for i in range(600))))
    monkeypatch.setattr(net,'fork_slug',lambda slug: slug)
    net.forks.get=lambda slug:(None,fp,{})
    net.forks._current_source=lambda fp,fn:'int f(){return 7;}'
    net.forks._meta=lambda fn:{}
    assert 'return 7' in agent_tokens.get_candidate(net,a,'game','f')
    assert 'op599' in agent_tokens.assembly(net,'game','f',start=590,limit=20)


def test_cached_compile_error_stays_actionable(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a');fn=net._function('f','game')
    net.remember_compile('error',a,fn,'COMPILE ERROR\nunknown type Widget',None)
    text=net.repeat_of('error',a,fn)['text']
    out=agent_tokens.feedback(net,a,'game','f',text,None)
    assert 'unknown type Widget' in out and 'Use last_diff' not in out


def test_compact_project_facts_never_trim_user_instructions(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(tools, '_project_info', lambda *args: 'facts '*1000)
    wish = 'Mandatory user instructions ' * 200
    net=SimpleNamespace(instructions=lambda slug:wish, overview=lambda slug:{}, fork_slug=lambda slug:None,
                        knowledge=SimpleNamespace(for_prompt=lambda slug:''))
    compact=tools.project_info(net,'game')
    assert wish in compact and len(compact)<len(tools.project_info(net,'game',detail=True))


def test_short_tool_descriptions_keep_extended_help(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    mcp=tools.build_mcp(net,lambda ctx:(a,'game'))
    definition=next(t for t in anyio.run(mcp.list_tools) if t.name=='compile_and_diff')
    extended=tools.guide_text('tool:compile_and_diff')
    assert len(definition.description)<len(extended) and 'Forks:' in extended


def test_claim_defers_supporting_sections_but_full_read_keeps_them(tmp_path, monkeypatch):
    net,_=_game(tmp_path,[('f',10,'open')]);a,_=net.local_agent('a')
    text='## CLAIMED: game/f\n### Notes\nlong previous investigation\n### Current source\n```c\nint f(){return 7;}\n```\n### Compiler\nkeep flags\n'
    out=agent_tokens.brief(net,text,'game')
    assert 'long previous investigation' not in out
    assert 'return 7' in out and 'keep flags' in out and 'get_function' in out
    net._record(net._function('f','game'),a,4,'int f(){return 7;}',1)
    monkeypatch.setattr(net, 'get_function', lambda *args: text)
    assert 'long previous investigation' in tools.call(net,a,'game','get_function',{'function':'f'})
    assert 'Recent history' in tools.call(net,a,'game','get_function',{'function':'f'})


def test_patch_prompt_reuses_revisions_and_prefers_small_edits():
    for prompt in (tools.WORK_PROMPT, tools.FORK_PROMPT):
        assert 'Prefer compile_patch for small edits' in prompt
        assert 'Do not fetch get_candidate after a successful patch' in prompt


def test_codex_invalid_effort_is_fatal_but_transient_errors_are_not():
    from decompnet.agents.codex import CODEX
    assert CODEX.fatal('{"code":"unsupported_value","param":"reasoning.effort"}')
    assert CODEX.fatal("Unsupported value: minimal is not supported with this model. Supported reasoning effort values are low and high")
    assert not CODEX.fatal('connection reset; retry later')
    assert not CODEX.fatal('rate limit exceeded')
