"""Repair example review/export is available only through the local app's guarded routes."""
import json

import pytest
from starlette.testclient import TestClient

from decompnet.app.engine import RomNetwork
from decompnet.app.server import build

from .test_app_server import PORT, SECRET, WINDOW


@pytest.fixture
def repair_app(tmp_path):
    home = tmp_path / '.decomp'
    home.mkdir()
    net = RomNetwork(home / 'app.db', tmp_path)
    mcp, manager, security, _ = build(net, home, PORT, SECRET)
    client = TestClient(mcp.streamable_http_app(transport_security=security, host='127.0.0.1'),
                        base_url=f'http://127.0.0.1:{PORT}', headers=WINDOW)
    return client, net, manager


def test_guarded_review_and_source_opt_in_export(repair_app, monkeypatch):
    client, net, manager = repair_app
    monkeypatch.setattr(net, 'on_match', lambda *a: None)
    agent, _ = net.local_agent('feedback-test')
    with net.store.tx() as db:
        pid = db.execute('INSERT INTO projects(slug,name,root,config) VALUES(?,?,?,?)',
                         ('feedback-demo', 'Feedback demo', '/unused', '{}')).lastrowid
        fid = db.execute('INSERT INTO functions(project_id,name,unit,status,n_insns,size,target) VALUES(?,?,?,?,?,?,?)',
                         (pid, 'target', '', 'open', 1, 4, b'test')).lastrowid
    current = lambda: net.store.one('SELECT * FROM functions WHERE id=?', fid)
    net._record(current(), agent, 9, 'private before code', 0)
    net._record(current(), agent, 0, 'private matching code', 0, matched=True)
    url = '/api/repair-examples?slug=feedback-demo'
    assert client.get(url, headers={'User-Agent': 'outside'}).status_code == 401
    listing = client.get(url).json()
    case = listing['examples'][0]['id']
    detail = client.get(url + f'&id={case}').json()
    assert detail['evidence']['after']['source'] == 'private matching code'
    assert client.get('/api/repair-examples').status_code == 400
    assert client.get('/api/repair-examples?all=1').status_code == 200
    assert client.get(url + '&category=invalid').status_code == 400
    posted = {'slug': 'feedback-demo', 'id': case, 'status': 'reviewed'}
    assert client.post('/api/repair-examples', json=posted).status_code == 403
    assert client.post('/api/repair-examples', json=posted, headers={'X-Decomp': '1'}).status_code == 200
    summary = client.get('/api/repair-examples/export?slug=feedback-demo')
    assert 'private matching code' not in summary.text and 'private before code' not in summary.text
    assert summary.headers['content-disposition'].endswith('"repair-examples.json"')
    full = client.get('/api/repair-examples/export?slug=feedback-demo&include_source=1')
    assert json.loads(full.text)['examples'][0]['evidence']['after']['source'] == 'private matching code'
    assert client.get('/api/repair-examples/export?slug=feedback-demo', headers={'User-Agent': 'outside'}).status_code == 401
