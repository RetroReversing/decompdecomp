"""Parallel activity remains attributed and bounded, independent of completion order."""
from concurrent.futures import ThreadPoolExecutor

from decompnet.app.deep_activity import Activity


def test_interleaved_workers_keep_their_own_output_and_finish_independently():
    snapshots = []
    class Log:
        def progress(self, value):
            snapshots.append(value)
    activity = Activity(Log())
    activity.publish({'matches': 3})
    def worker(name):
        for i in range(12):
            activity.update(name, {'tested': i, 'best_percent': 90+i/10}, line=f'{name}: {i}')
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(worker, ['a', 'b']))
    rows = {w['function']: w for w in snapshots[-1]['workers']}
    assert rows.keys() == {'a', 'b'}
    assert all(len(w['lines']) == 6 for w in rows.values())
    assert all(line.startswith(name) for name, w in rows.items() for line in w['lines'])
    activity.update('a', {'phase': 'finished'}, finished=True)
    assert [w['function'] for w in snapshots[-1]['workers']] == ['b']
    assert snapshots[-1]['recent_workers'][0]['function'] == 'a'
    assert snapshots[-1]['matches'] == 3
    for i in range(20):
        activity.update(str(i), finished=True)
    assert len(snapshots[-1]['recent_workers']) == 8
    assert len(snapshots[1]['workers'][0]['lines']) == 1
