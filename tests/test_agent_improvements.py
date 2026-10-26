"""Historical partial improvements remain credited after another worker finishes."""
import json

from decompnet.app.engine import RomNetwork


def test_distinct_improvements_ignore_ties_failures_and_other_projects(tmp_path):
    net = RomNetwork(tmp_path / 'app.db', tmp_path)
    first, _ = net.local_agent('first')
    second, _ = net.local_agent('second')
    with net.store.tx() as db:
        for slug in ('demo', 'other'):
            pid = db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                             (slug, slug, 'mips', str(tmp_path), json.dumps({'kind': 'rom'}))).lastrowid
            for name in ('one', 'two'):
                fid = db.execute('INSERT INTO functions(project_id,name,size,n_insns) VALUES(?,?,?,?)',
                                 (pid, name, 40, 10)).lastrowid
                for agent, score, compiled in [(first.id, None, 0), (first.id, 1000, 1),
                                               (first.id, 800, 1), (first.id, 700, 1),
                                               (second.id, 700, 1), (second.id, 900, 1),
                                               (second.id, 600, 0)]:
                    db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(?,?,?,?,?)',
                               (fid, agent, score, compiled, 1))
                if name == 'one':
                    db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(?,?,?,?,?)',
                               (fid, second.id, 0, 1, 1))
                    db.execute("UPDATE functions SET status='matched',matched_agent_id=? WHERE id=?", (second.id, fid))
    board = {r['agent']: r for r in net.stats('demo')['agents']}
    assert board['first']['improved'] == 2  # repeated improvements to each function count once
    assert board['first']['matched'] == 0
    assert board['second']['improved'] == 1  # direct match counts, ties and failures do not
    assert board['second']['matched'] == 1
