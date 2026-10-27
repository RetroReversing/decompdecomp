"""Keep automatic workers from passing an unchanged failed candidate around indefinitely."""
import hashlib
import json

VERSION = 'local-repairs-v8'
RELEASES = 3
PREFIX = 'agent-stall:'


def common_state(net, proj):
    contexts = net.store.all('SELECT unit,MAX(version) AS version FROM context_versions WHERE project_id=? GROUP BY unit ORDER BY unit',proj['id'])
    symbols = net.store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name',proj['id'])
    state = [VERSION,proj['config'],[(r['unit'],r['version']) for r in contexts],
             [tuple(r) for r in symbols],getattr(net,'_generation',{}).get(proj['id'],0)]
    fp = None
    if getattr(net,'fork_slug',lambda slug: None)(proj['slug']):
        _, fp, _ = net.forks.get(proj['slug'])
        paths = [fp.root / p for p in ('Makefile','build.ninja','objdiff.json','configure.py')]
        # Observe external build/header edits without walking generated objects.
        paths += sorted((fp.root/'include').rglob('*.h')) if (fp.root/'include').is_dir() else []
        state.append([(str(p),file_digest(p)) for p in paths])
    return hashlib.sha256(repr(state).encode()).hexdigest(),fp


def file_digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return 'unavailable'


def signature(net, proj, fn, common=None):
    stamp,fp = common if common is not None else common_state(net,proj)
    state = [stamp,fn['best_source'],fn['target'],fn['relocs']]
    if fp is not None:
        unit = fp.unit(fn['unit'])
        state.append(file_digest(fp.root/unit.source) if unit.source else None)
    return hashlib.sha256(repr(state).encode()).hexdigest()


def released(net, fn):
    proj = net.store.one('SELECT * FROM projects WHERE id=?',fn['project_id'])
    current = net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
    if current['status'] != 'open' or not current['best_source']:
        return
    stamp = signature(net,proj,current)
    key = PREFIX+str(fn['id'])
    with net.store.tx() as db:
        row = db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        old = json.loads(row['value']) if row else {}
        count = old.get('count',0)+1 if old.get('stamp') == stamp else 1
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
