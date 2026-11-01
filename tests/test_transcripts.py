"""Full transcripts survive feed eviction and restart, without exposing credentials or other projects."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app.agents import Manager, Session
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def setup(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                   ('demo', 'Synthetic transcript project', 'mips', str(tmp_path), '{}'))
    manager = Manager(net, tmp_path, 'http://127.0.0.1/mcp')
    session = Session('1', 'codex', 'helper', 'demo', 'helper', {'api_key': 'private-key'})
    manager.sessions['1'] = session
    row = manager._session_begin(session)
    return net, manager, session, row


def test_full_text_survives_short_feed_and_restart(setup, tmp_path):
    net, manager, session, row = setup
    text = 'full prompt ' * 1000
    session.record('prompt', text)
    for i in range(405):
        session.push('text', f'reply {i}')
    assert len(session.feed) == 400
    assert manager.transcript('demo', 1)['events'][0]['text'] == text
    restored = Manager(net, tmp_path, 'http://127.0.0.1/mcp')
    first = restored.transcript('demo', 1, row)
    assert first['more'] and len(first['events']) == 25
    second = restored.transcript('demo', 1, row, first['events'][-1]['id'])
    assert second['events'][0]['id'] > first['events'][-1]['id']
    assert first['events'][0]['kind'] == 'prompt'


def test_redaction_and_project_ownership(setup):
    net, manager, session, row = setup
    session.record('tool_request', {'api_key': 'unknown-key', 'code': 'private-key', 'password': 'password-value'})
    session.record('tool_response', 'Authorization: Bearer bearer-secret')
    text = json.dumps(manager.transcript('demo', 1)['events'])
    assert all(value not in text for value in ('unknown-key', 'private-key', 'password-value', 'bearer-secret'))
    with pytest.raises(NetworkError):
        manager.transcript('other', 1, row)
    with pytest.raises(NetworkError):
        manager.transcript('demo', 2, row)


def test_tool_exchange_and_live_usage(setup):
    net, manager, session, row = setup
    agent = SimpleNamespace(name='helper')
    manager.record_agent(agent, 'demo', 'tool_request', {'name': 'get_function', 'arguments': {'function': 'target'}})
    manager.record_agent(agent, 'demo', 'tool_response', {'content': 'full source ' * 1000})
    session.usage.update(input=123, output=12, cache_read=456)
    data = manager.transcript('demo', 1)
    assert len(data['events'][1]['text']) > 4000
    assert data['session']['input_tokens'] == 123
    assert data['session']['cache_read_tokens'] == 456
    manager.record_agent(agent, 'other', 'tool_request', 'not recorded')
    assert len(manager.transcript('demo', 1)['events']) == 2


def test_old_sessions_are_not_fabricated_and_session_paging(setup):
    net, manager, session, row = setup
    for _ in range(35):
        manager._session_begin(session)
    first = manager.transcript('demo', 1)
    assert len(first['sessions']) == 30 and not first['events']
    second = manager.transcript('demo', 1, before=first['sessions'][-1]['id'])
    assert len(second['sessions']) == 6
    assert second['sessions'][0]['id'] < first['sessions'][-1]['id']


def test_mcp_records_full_arguments_results_and_errors(setup, monkeypatch):
    import anyio
    from mcp.server.mcpserver.exceptions import ToolError

    from decompnet.app import tools
    net, manager, session, row = setup
    agent = SimpleNamespace(name='helper')
    monkeypatch.setitem(tools.HANDLERS, 'get_function', lambda *a, **kw: 'source ' * 1000)
    monkeypatch.setattr(tools, '_named', lambda *a: a[-1])
    mcp = tools.build_mcp(net, lambda context: (agent, 'demo'), observe=manager.record_agent)
    anyio.run(mcp.call_tool, 'get_function', {'function': 'target'})
    events = manager.transcript('demo', 1)['events']
    assert json.loads(events[0]['text'])['arguments']['function'] == 'target'
    assert len(json.loads(events[1]['text'])['content']) == 7000
    first_id = json.loads(events[0]['text'])['call_id']
    assert first_id and first_id == json.loads(events[1]['text'])['call_id']

    def fail(*args, **kw):
        raise NetworkError('Missing function.')
    monkeypatch.setitem(tools.HANDLERS, 'get_function', fail)
    with pytest.raises(ToolError, match='Missing function'):
        anyio.run(mcp.call_tool, 'get_function', {'function': 'missing'})
    events = manager.transcript('demo', 1)['events']
    assert 'Missing function.' in events[-1]['text']
    assert json.loads(events[-2]['text'])['call_id'] == json.loads(events[-1]['text'])['call_id']
    assert json.loads(events[-1]['text'])['call_id'] != first_id
