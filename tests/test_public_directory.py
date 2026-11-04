"""Publishing is explicit and metadata-only for both project and ROM rooms."""
import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from decompnet.app import collab, public_directory
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def public_room(tmp_path):
    net = RomNetwork(tmp_path / 'db', tmp_path)
    cfg = {'rom': {'title': 'Demo', 'sha1': 'PRIVATE HASH'},
           'compiler': {'toolchain': 'demo-cc'}, 'collab': {'visibility': 'public'}}
    with net.store.tx() as db:
        db.execute('INSERT INTO projects(id,slug,name,root,config,arch) VALUES(1,?,?,?,?,?)',
                   ('demo', 'Demo project', str(tmp_path), json.dumps(cfg), 'mips'))
        db.execute('INSERT INTO functions(project_id,name,seed,asm,target,size,n_insns,unit) VALUES(1,?,?,?,?,?,?,?)',
                   ('demo_fn', 'SECRET SOURCE', 'SECRET ASSEMBLY', b'GAME BYTES', 32, 8, 'private/path.c'))
    room = collab.Room(net, 'demo')
    net.rooms['demo'] = room
    return SimpleNamespace(net=net, room=room, client=TestClient(collab.host_app(net, net.rooms)))


def test_public_room_does_not_imply_public_metadata(public_room):
    response = public_room.client.get('/directory/project?room=demo')
    assert response.status_code == 410
    assert 'functions' not in response.json()
    assert public_room.client.post('/directory/project?room=demo', json={'enabled': True}).status_code == 405
    assert public_room.client.post('/api/collab/directory', json={'enabled': True}).status_code == 404


def test_snapshot_exposes_only_metadata_and_consent_survives_new_room(public_room):
    public_directory.configure(public_room.net, 'demo', True, 'a' * 64)
    response = public_room.client.get('/directory/project')
    assert response.status_code == 200
    data = response.json()
    assert data['summary'] == {'functions': 1, 'matched': 0, 'bytes': 32, 'matched_bytes': 0}
    assert data['functions'] == [['demo_fn', 32, 'open', None, 8]]
    assert data['proof'] == 'a' * 64
    for secret in ('SECRET', 'GAME BYTES', 'PRIVATE HASH', 'private/path', 'token', 'source', 'transcript'):
        assert secret not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert public_room.client.get('/directory/project', headers={'If-None-Match': response.headers['etag']}).status_code == 304
    replacement = collab.Room(public_room.net, 'demo')
    assert public_directory.snapshot(public_room.net, replacement)['proof'] == 'a' * 64


def test_private_rooms_and_withdrawn_consent_never_publish(public_room):
    public_directory.configure(public_room.net, 'demo', True, 'b' * 64)
    public_room.room.configure('private')
    assert public_room.client.get('/directory/project?room=demo').status_code == 410
    public_room.room.configure('public')
    public_directory.configure(public_room.net, 'demo', False, '')
    assert public_room.client.get('/directory/project?room=demo').status_code == 410
    with pytest.raises(NetworkError):
        public_directory.configure(public_room.net, 'demo', True, 'short')


def test_fork_rooms_use_same_metadata_allowlist(public_room):
    public_directory.configure(public_room.net, 'demo', True, 'a' * 64)
    public_room.room.fork = True
    public_room.net.fork_slug = lambda slug: True
    assert public_directory.snapshot(public_room.net, public_room.room)['functions'][0][0] == 'demo_fn'
