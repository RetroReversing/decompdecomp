import json

import pytest

from decompnet.app import tool_policy, tools
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError
from tests.test_claims import _game


def test_tool_lists_and_dispatch_obey_global_and_per_agent_policy(tmp_path, monkeypatch):
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    a, _ = net.local_agent('one')
    b, _ = net.local_agent('two')
    net.set_selection('game', a.name, {'disabled_tools':['guide']})
    assert 'guide' not in tools.project_tools(net, 'game', a)
    from decompnet.agents.api import api_tool_schemas
    assert 'guide' not in {t['function']['name'] for t in api_tool_schemas(names=tools.project_tools(net, 'game', a))}
    assert 'guide' in tools.project_tools(net, 'game', b)
    tool_policy.set_global(net, 'search_notes', False)
    assert 'search_notes' not in tools.project_tools(net, 'game', b)
    with pytest.raises(NetworkError, match='disabled'):
        tools._dispatch(net, a, 'game', 'guide', {'topic':'mips'})
    assert not net.store.one('SELECT * FROM agent_tool_usage')
    monkeypatch.setitem(tools.HANDLERS, 'guide', lambda *args, **kwargs: 'guide text')
    monkeypatch.setattr('decompnet.app.agent_local_pass.after_tool', lambda *args: args[-1])
    assert tools._dispatch(net, b, 'game', 'guide', {}) == 'guide text'
    assert net.store.one('SELECT calls FROM agent_tool_usage WHERE name=?','guide')['calls'] == 1
    def fails(*args, **kwargs):
        raise NetworkError('A recorded tool failure')
    monkeypatch.setitem(tools.HANDLERS, 'guide', fails)
    with pytest.raises(NetworkError, match='recorded tool failure'):
        tools._dispatch(net, b, 'game', 'guide', {})
    assert net.store.one('SELECT calls FROM agent_tool_usage WHERE name=?','guide')['calls'] == 2

    for names in (['claim_function'], ['unknown'], 'guide'):
        with pytest.raises(NetworkError):
            net.validate_selection({'disabled_tools':names})


def test_usage_backfill_is_once_and_counts_survive_restart(tmp_path):
    from decompnet.app.transcripts import Transcripts
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    transcripts = Transcripts(net.store)
    transcripts.append(1, 'tool_request', {'name':'guide','arguments':{'topic':'mips'}})
    transcripts.append(1, 'tool_response', {'name':'guide','content':'not a second call'})
    tool_policy.ensure(net)
    agent, _ = net.local_agent('reader')
    tool_policy.record(net, agent, 'game', 'guide')
    # Read the actual backing database path from SQLite, independent of fixture naming.
    path = net.store.one('PRAGMA database_list')['file']
    with net.store.tx() as db:
        db.execute('DELETE FROM helper_messages')
    reopened = RomNetwork(path, tmp_path)
    tool_policy.ensure(reopened)
    assert reopened.store.one('SELECT calls FROM agent_tool_usage WHERE name=?','guide')['calls'] == 2
    assert json.loads(reopened.store.one('SELECT value FROM settings WHERE key=?','tool-usage-started')['value']) > 0


def test_helper_tool_policy_survives_other_work_settings(tmp_path):
    from decompnet.app.agents import Manager, Session
    net, _ = _game(tmp_path, [('f', 10, 'open')])
    manager = Manager(net, tmp_path, 'http://127.0.0.1/mcp')
    session = Session('1', 'codex', 'reader', 'game', 'reader', {})
    manager.sessions['1'] = session
    manager.set_tool('1', 'guide', False)
    manager.set_tool('1', 'search_notes', False)
    manager.set_selection('1', {'strategy':'random'})
    assert net.selection('game','reader')['disabled_tools'] == ['guide','search_notes']
    manager.set_tool('1', 'guide', True)
    assert session.options['selection']['disabled_tools'] == ['search_notes']
