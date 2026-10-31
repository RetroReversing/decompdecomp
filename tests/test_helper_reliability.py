"""Broken tool connections and stale model selections stop before paid work."""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from decompnet.agents.zcode import ZCODE
from decompnet.app.agents import Fatal, Manager
from decompnet.app.engine import RomNetwork


@pytest.mark.parametrize('missing', [False, True])
def test_mcp_preflight_checks_actual_advertised_tools(monkeypatch, missing):
    import mcp
    import mcp.client.streamable_http as transport
    import mcp.shared._httpx_utils as http

    seen = []

    @asynccontextmanager
    async def client(**kwargs):
        seen.append(kwargs)
        yield object()

    @asynccontextmanager
    async def stream(url, **kwargs):
        seen.append(url)
        yield (None, None, None)

    class Session:
        def __init__(self, *args): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def initialize(self): seen.append('initialize')
        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in
                (['claim_function'] if missing else ['claim_function', 'compile_and_diff', 'release_function'])])

    monkeypatch.setattr(http, 'create_mcp_http_client', client)
    monkeypatch.setattr(transport, 'streamable_http_client', stream)
    monkeypatch.setattr(mcp, 'ClientSession', Session)
    manager = Manager.__new__(Manager)
    manager.net = SimpleNamespace(authenticate=lambda token: seen.append('authenticated'))
    manager.mcp_url = 'http://127.0.0.1:1234/mcp'
    if missing:
        with pytest.raises(Fatal, match='No model request'):
            manager._preflight_tools('test-token')
    else:
        manager._preflight_tools('test-token')
    assert seen[0] == 'authenticated'
    assert seen[1]['headers'] == {'Authorization': 'Bearer test-token'}
    assert 'initialize' in seen


def test_restart_closes_only_unfinished_sessions(tmp_path, monkeypatch):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    monkeypatch.setattr(Manager, '_restore', lambda self: None)
    monkeypatch.setattr(Manager, 'refresh_detected', lambda self: None)
    Manager(net, tmp_path, 'http://127.0.0.1:1/mcp')
    with net.store.tx() as db:
        for ended, outcome in [(None, ''), (10, 'ok')]:
            db.execute('INSERT INTO helper_sessions(helper_id,project,kind,model,agent,started_at,ended_at,outcome) VALUES(?,?,?,?,?,?,?,?)',
                       (1, 'game', 'claude', '', 'helper', 1, ended, outcome))
    Manager(net, tmp_path, 'http://127.0.0.1:1/mcp')
    rows = net.store.all('SELECT ended_at,outcome FROM helper_sessions ORDER BY id')
    assert rows[0]['ended_at'] and rows[0]['outcome'] == 'interrupted'
    assert rows[1]['ended_at'] == 10 and rows[1]['outcome'] == 'ok'


def test_stale_model_selection_is_validated_before_launch(monkeypatch):
    from decompnet.agents import zcode
    monkeypatch.setattr(zcode, 'catalog', lambda: [{'id': 'provider/current'}])
    assert ZCODE.validate_model('provider/current#high') is None
    assert 'Refresh the agent list' in ZCODE.validate_model('provider/removed')


def test_batch_excludes_already_held_and_returned_functions(tmp_path, monkeypatch):
    from decompnet.network import Network
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    agent, _ = net.local_agent('test')
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)', ('game','Game',str(tmp_path),json.dumps({'kind':'rom'}))).lastrowid
        ids = [db.execute('INSERT INTO functions(project_id,name,size,n_insns) VALUES(?,?,?,?)', (pid, f'f{i}', 16, 4)).lastrowid for i in range(3)]
        db.execute('INSERT INTO claims VALUES(?,?,?,?)', (ids[0], agent.id, 1, 9999999999))
    monkeypatch.setattr(net, 'sdk_functions', lambda proj: set())
    calls = []
    def claim(self, agent, project, **kwargs):
        calls.append(list(kwargs['batch_from']['deferred']))
        return {'id': ids[len(calls)], 'name': f'f{len(calls)}'}
    monkeypatch.setattr(Network, 'claim_row', claim)
    rows = net._batch_more(agent, {'id':pid,'slug':'game'}, {'id':ids[0],'unit':''}, 2)
    assert len(rows) == 2
    assert ids[0] in calls[0] and ids[1] in calls[1]


def test_preflight_against_real_isolated_app(tmp_path, monkeypatch):
    import socket
    import threading
    import time

    import uvicorn

    from decompnet.app.server import build

    monkeypatch.setattr(Manager, 'refresh_detected', lambda self: None)
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        mcp, manager, security, _ = build(net, tmp_path, port, 'test-window-secret')
        app = mcp.streamable_http_app(transport_security=security, host='127.0.0.1')
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and time.monotonic() < deadline:
                time.sleep(.02)
            assert server.started
            _, token = net.local_agent('test-helper')
            manager._preflight_tools(token)
            with pytest.raises(Fatal, match='No model request'):
                manager._preflight_tools('invalid')
        finally:
            server.should_exit = True
            thread.join(timeout=5)


@pytest.mark.parametrize('message', ['invalid API token', "The decomp tools aren't available in this session", 'no decomp tools are available'])
def test_lost_tool_connection_pauses_instead_of_paid_retries(tmp_path, monkeypatch, message):
    import os
    import sys

    from decompnet.app.agents import Session

    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    monkeypatch.setattr(Manager, 'refresh_detected', lambda self: None)
    manager = Manager(net, tmp_path, 'http://127.0.0.1:1/mcp')
    monkeypatch.setattr(manager, '_preflight_tools', lambda token: None)
    event = {'type':'assistant', 'message':{'content':[{'type':'text', 'text':message}]}}
    code = f'print({json.dumps(event)!r})'
    monkeypatch.setattr(manager, '_command', lambda *args: ([sys.executable, '-c', code], dict(os.environ), ''))
    session = Session('1', 'claude', 'helper', 'game', 'helper', {})
    with pytest.raises(Fatal, match='lost access'):
        manager._run_cli(session, 'test-token', tmp_path)
