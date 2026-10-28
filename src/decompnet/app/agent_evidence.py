"""Scoped baseline evidence and an independent unchanged-instruction budget; never verdict shortcuts."""
import hashlib
import json

from .. import scoring
from ..forks.project import ForkError
from ..network import NetworkError


def scope(net,agent,fn):
    from .agent_tokens import target_scope
    claim=net.store.one('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
    return (agent.id,fn['id'],claim['claimed_at'] if claim else None,target_scope(net,fn,''),
            hashlib.sha256(repr((fn['target'],fn['relocs'],fn['vram'],getattr(net,'_generation',{}).get(fn['project_id'],0))).encode()).hexdigest())


def baseline(net,agent,slug,fn,source):
    try:
        return _baseline(net,agent,slug,fn,source)
    except (ForkError,NetworkError,ValueError,KeyError,OSError):
        # Unavailable build inputs leave the historical baseline explicitly unverified; never cache an error.
        return None


def _baseline(net,agent,slug,fn,source):
    if not hasattr(net,'fork_slug'):
        return None
    key=scope(net,agent,fn)+(hashlib.sha256(source.encode()).hexdigest(),)
    cache=getattr(net,'_baseline_evidence',{})
    net._baseline_evidence=cache
    if key in cache:
        return cache[key]
    proj=net._project(slug)
    # Core coordination-only projects need not have a local judge/compiler.
    if not net.fork_slug(slug) and not json.loads(proj['config']).get('compiler'):
        return None
    if net.fork_slug(slug):
        try:
            _, fp, _ = net.forks.get(slug)
            fp.unit(fn['unit'])
        except ForkError:
            return None
        result=net.forks._attempt(agent,slug,fn['name'],source,keep=False,diagnostic=True)
        if result.get('score') is None:
            return None
        measured={'score':result['score'],'rows':result.get('rows'),'source':source,'verified':True,'exact':result['matched']}
    else:
        context=net._context(proj['id'],fn['unit'])
        if context is None:
            return None
        compiled=net._compile(proj,context['header'],source,fn)
        if not compiled.ok:
            return None
        candidate,exact,_=net._evaluate(fn,proj,compiled.obj)
        if candidate is None:
            return None
        diff=scoring.diff(net._target_obj(fn,proj),candidate)
        measured={'score':0 if exact else max(1,diff.score),'rows':diff.rows,'source':source,'verified':True,'exact':bool(exact),'legend':scoring.LEGEND}
    # Imported diagnostic restoration can advance the compile generation. Cache against the
    # restored state, so the next read reuses evidence for that state rather than rebuilding.
    key=scope(net,agent,fn)+(hashlib.sha256(source.encode()).hexdigest(),)
    cache[key]=measured
    if measured.get('rows') is not None:
        states=getattr(net,'_unchanged_output',{})
        net._unchanged_output=states
        fingerprint=hashlib.sha256(repr((measured['score'],measured['rows'])).encode()).hexdigest()
        states.setdefault(scope(net,agent,fn),{'seen':{fingerprint},'repeated':0})
    while len(cache)>64:
        del cache[next(iter(cache))]
    return measured


def track(net,agent,slug,fn):
    measured=getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
    if not measured or measured.get('rows') is None or measured.get('score') is None:
        return None
    key=scope(net,agent,fn)
    states=getattr(net,'_unchanged_output',{})
    net._unchanged_output=states
    fingerprint=hashlib.sha256(repr((measured['score'],measured['rows'])).encode()).hexdigest()
    prior=states.get(key,{'seen':set(),'repeated':0})
    count=prior['repeated']+1 if fingerprint in prior['seen'] else 0
    seen=prior['seen']
    seen.add(fingerprint)
    # Bounded evidence, never an unbounded log of candidate source or code.
    if len(seen)>512:
        seen={fingerprint}
        count=0
    states[key]={'seen':seen,'repeated':count}
    while len(states)>128:
        del states[next(iter(states))]
    limit=net.selection(slug,agent.name).get('unchanged_compiles',8)
    if limit and count>=limit and net._function(fn['name'],slug)['status']=='open':
        held=net.store.one('SELECT 1 FROM claims WHERE function_id=? AND agent_id=?',fn['id'],agent.id)
        if held:
            result=net.release(agent,fn['name'],f'Moved on after {count} trials returning previously seen scored instruction diffs. Best source retained.',slug)
            from .. import claim_stalls
            claim_stalls.released(net,fn,exhausted=True)
            return 'Unchanged-output limit reached. '+result+' Automatic claims pause until the candidate, build context or repair rules change.'
    return f'Repeated instruction outputs without novelty: {count}/{limit}.' if limit else None


def warning(net,agent,fn,text):
    if not text:
        return ''
    key=scope(net,agent,fn)+(hashlib.sha256(text.encode()).hexdigest(),)
    seen=getattr(net,'_warning_evidence',{})
    net._warning_evidence=seen
    if key in seen:
        return 'Same compiler warnings as earlier in this claim; full text: get_function(section=warnings).'
    seen[key]=text
    while len(seen)>128:
        del seen[next(iter(seen))]
    return text[:250]


def warnings(net,agent,fn):
    key=scope(net,agent,fn)
    values=[v for k,v in getattr(net,'_warning_evidence',{}).items() if k[:-1]==key]
    return '\n\n'.join(values) or 'No compiler warnings retained for this claim.'
