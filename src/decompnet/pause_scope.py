"""Recorded session model identity for exhaustion; worker names are never model evidence."""
import hashlib


def identity(net, proj, agent):
    if agent is None:return '', 'legacy unattributed pauses'
    name=agent.name if hasattr(agent,'name') else agent['name']
    tables={r['name'] for r in net.store.all("SELECT name FROM sqlite_master WHERE type='table'")}
    active=net.store.one('SELECT model FROM helper_sessions WHERE project=? AND agent=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1',proj['slug'],name) if 'helper_sessions' in tables else None
    helper=net.store.one('SELECT model FROM helpers WHERE project=? AND name=?',proj['slug'],name) if 'helpers' in tables else None
    model=((active['model'] if active else '') or (helper['model'] if helper else '') or '').strip()
    if model.lower() in {'auto','default','automatic'}:model=''
    # Preserve explicit effort/provider qualifiers. Defaults do not identify a model.
    value='model:'+model if model else 'worker:'+str(agent.id if hasattr(agent,'id') else agent['id'])
    return ':'+hashlib.sha256(value.encode()).hexdigest(), model or 'this worker (model unspecified)'
