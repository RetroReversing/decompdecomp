"""ROM-reference setup is a scoped, stoppable and deduplicated job."""
import threading
import time

from tests.test_app_server import app  # noqa: F401


def test_reference_setup_job_is_scoped_and_reconnectable(app, tmp_path, monkeypatch):  # noqa: F811
    client, net, _ = app
    root, exported = tmp_path/'public', tmp_path/'export'
    root.mkdir()
    exported.mkdir()
    net.forks._register('public', 'Public', 'fork', root,
                        {'kind':'fork', 'platform':'n64', 'game_sha1':'1'*40, 'ready':False}, 'x')
    entered, release = threading.Event(), threading.Event()
    def setup(slug, path, log):
        assert slug == 'public' and path == exported
        entered.set()
        assert release.wait(5)
        log.check_cancel()
        return {'slug':slug}
    monkeypatch.setattr(net.forks, 'adopt_rom_reference', setup)
    monkeypatch.setattr(net.forks, 'needs', lambda slug:{'rom_reference_available':True})
    headers = {'X-Decomp':'1'}
    data = {'slug':'public', 'path':str(exported)}
    try:
        first = client.post('/api/fork/rom-reference', headers=headers, json=data)
        assert first.status_code == 200, first.text
        job = first.json()['job']
        assert entered.wait(2)
        duplicate = client.post('/api/fork/rom-reference', headers=headers, json=data)
        assert duplicate.json()['job'] == job
        needs = client.get('/api/fork/needs', params={'slug':'public'}).json()
        assert needs['rom_reference_job']['id'] == job
        assert client.get('/api/job', params={'id':job}).json()['activity_project'] == 'public'
        assert client.post('/api/job/cancel', headers=headers, json={'id':job}).status_code == 200
    finally:
        release.set()
    for _ in range(100):
        state = client.get('/api/job', params={'id':job}).json()['state']
        if state != 'running':
            break
        time.sleep(.01)
    assert state == 'cancelled'
