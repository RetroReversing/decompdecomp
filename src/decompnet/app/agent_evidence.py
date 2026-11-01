"""Scoped baseline evidence and an independent unchanged-instruction budget; never verdict shortcuts."""
import hashlib
import json

from .. import scoring, toolchains
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


def _baseline(net,agent,slug,fn,source,diagnostic=False):
    if not hasattr(net,'fork_slug'):
        return None
    key=scope(net,agent,fn)+(hashlib.sha256(source.encode()).hexdigest(),)
    cache=getattr(net,'_baseline_evidence',{})
    net._baseline_evidence=cache
    if key in cache:
        return cache[key]
    failures=getattr(net, '_baseline_failures', {})
    net._baseline_failures=failures
    if diagnostic and key in failures:
        latest=net.store.one('SELECT MAX(id) AS id FROM attempts WHERE function_id=?',fn['id'])['id'] or 0
        if latest <= failures[key].get('after_attempt',0):
            return failures[key]
        del failures[key]  # an explicit compile may have fixed an external build/resource problem
    def failed(message):
        result={'score':None, 'source':source, 'rows':None, 'error':str(message)[:1000],
                'after_attempt':net.store.one('SELECT MAX(id) AS id FROM attempts WHERE function_id=?',fn['id'])['id'] or 0}
        restored_key=scope(net,agent,fn)+(hashlib.sha256(source.encode()).hexdigest(),)
        failures[restored_key]=result
        while len(failures)>64:
            del failures[next(iter(failures))]
        return result if diagnostic else None
    proj=net._project(slug)
    # Core coordination-only projects need not have a local judge/compiler.
    if not net.fork_slug(slug) and not json.loads(proj['config']).get('compiler'):
        return None
    if net.fork_slug(slug):
        try:
            _, fp, _ = net.forks.get(slug)
            fp.unit(fn['unit'])
        except ForkError as error:
            return failed(str(error))
        result=net.forks._attempt(agent,slug,fn['name'],source,keep=False,diagnostic=True)
        if result.get('score') is None:
            return failed(result.get('error') or result.get('diff') or result.get('message') or result.get('text') or 'The project baseline could not be built/scored.')
        measured={'score':result['score'],'rows':result.get('rows'),'source':source,'verified':True,'exact':result['matched'],'line_note':result.get('line_note','')}
    else:
        context=net._context(proj['id'],fn['unit'])
        if context is None:
            return None
        compiled=net._compile(proj,context['header'],source,fn)
        if not compiled.ok:
            return failed(compiled.log or "The baseline does not compile.")
        candidate,exact,problems=net._evaluate(fn,proj,compiled.obj)
        if candidate is None:
            return failed("The baseline could not be linked/scored. " + "; ".join(problems or []))
        diff=scoring.diff(net._target_obj(fn,proj),candidate)
        measured={'score':0 if exact else max(1,diff.score),'rows':diff.rows,'source':source,'verified':True,'exact':bool(exact),'legend':scoring.LEGEND}
        from ..source_lines import mismatches, render
        mapped = mismatches(compiled.obj, fn['name'], diff.rows, source,
                            filename='func.cpp' if getattr(compiled,'language','c') == 'c++' else 'func.c')
        measured['line_note'] = render(mapped, source, limit=4)

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


def preflight(net,agent,slug,fn,source):
    """One scoped baseline check before machine-generated variants; failures do not consume a claim try."""
    try:
        return _baseline(net,agent,slug,fn,source,diagnostic=True)
    except (toolchains.ToolchainError,ForkError,NetworkError,OSError) as error:
        return {'score':None, 'rows':None, 'source':source, 'error':str(error)[:1000]}
    except (ValueError,KeyError):
        # Coordination-only projects may not expose a local judge. No verdict is inferred.
        return None


def blocked_message(report, revision):
    return ('Baseline blocked; no variants submitted. Fix the candidate declarations/build inputs or redraft first.\n'
            + report['error'] + f'\nCandidate revision {revision}. This failure is cached until inputs change.')


def track(net,agent,slug,fn):
    measured=getattr(net,'_agent_outcomes',{}).get((agent.id,fn['id']))
    if not measured or measured.get('rows') is None or measured.get('score') is None:
        return None
    from .progress_rate import track as progress_rate
    moved=progress_rate(net,agent,slug,fn)
    if moved:
        return moved
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
