"""Keep automatic workers from passing an unchanged failed candidate around indefinitely."""
import hashlib
import json
from pathlib import Path

VERSION = 'local-repairs-v16'
RELEASES = 3
PREFIX = 'agent-stall:'


def common_state(net, proj):
    # Kept as a cheap shared stamp; function-specific build dependencies are hashed below.
    return [VERSION,repair_rules()]


def repair_rules():
    """Persist across restarts; invalidate when installed repair implementations change."""
    app = Path(__file__).parent / 'app'
    paths = sorted(set(app.glob('*repairs.py')) | {app / 'local_experiments.py'})
    return [(p.name,file_digest(p)) for p in paths]


def file_digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return 'unavailable'


def signature(net, proj, fn, common=None):
    from . import candidate_scope
    stamp = common if common is not None else common_state(net,proj)
    return hashlib.sha256(repr((stamp,candidate_scope.key(net,proj,fn),json.loads(proj['config']).get('instructions'),fn['best_source'])).encode()).hexdigest()


def released(net, fn, *, exhausted=False):
    proj = net.store.one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    current = net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
    if current['status'] != 'open' or not current['best_source']:
        return
    stamp = signature(net,proj,current)
    key = PREFIX+str(fn['id'])
    with net.store.tx() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        old = json.loads(row['value']) if row else {}
        count = RELEASES if exhausted else (old.get('count',0)+1 if old.get('stamp') == stamp else 1)
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps({'stamp':stamp,'count':count})))


def blocked(net, proj):
    rows = net.store.all('SELECT f.*,s.value AS stall FROM functions f JOIN settings s ON s.key=? || f.id WHERE f.project_id=? AND f.status=\'open\'',PREFIX,proj['id'])
    result = []
    common = common_state(net,proj) if rows else None
    for fn in rows:
        old = json.loads(fn['stall'])
        if old.get('count',0) >= RELEASES and old.get('stamp') == signature(net,proj,fn,common):
            result.append(fn['id'])
    return result
