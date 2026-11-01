"""Bounded current-toolchain flag checks before a long ROM search, cached by actual judge inputs."""
import hashlib
import json
import threading

from .. import compiler, toolchains
from ..network import NetworkError
from ..project import compiler_for
from .candidate_diagnostics import unresolved

MAX_PROFILES = 4


class PreflightStopped(Exception):
    pass


def run(net, proj, fn, log=lambda text: None, check=lambda: None, max_profiles=MAX_PROFILES):
    """Only one preflight per function; another worker can continue without duplicating probes."""
    locks = net.__dict__.setdefault('_matching_preflight_locks', {})
    lock = locks.setdefault(fn['id'], threading.Lock())
    if not lock.acquire(blocking=False):
        return {'tested': 0}
    try:
        return _run(net, proj, fn, log, check, max_profiles)
    finally:
        lock.release()


def _run(net, proj, fn, log, check, max_profiles):
    """Use installed profiles only; an applied winner is rejudged and scoped by the existing settings gate."""
    if max_profiles <= 0:
        return {'tested': 0}
    if (not hasattr(net, '_comparison_digest') or net.fork_slug(proj['slug']) or not net.is_rom(proj)
            or fn['status'] != 'open' or not fn['best_source']):
        return {'tested': 0}
    cfg = net.cfg(proj)
    own = compiler_for(cfg, fn['vram'])
    if own.get('cmd') or not own.get('toolchain') or toolchains.installed(own['toolchain']) is None:
        return {'tested': 0}
    from .profile_hypotheses import neighbours, prioritized
    choices = [list(own['flags'])]
    for flags in prioritized(proj, fn, own)+neighbours(net, proj, fn, own):
        if flags not in choices:choices.append(flags)
    # Optimization hypotheses precede debug variants; do not spend the whole budget on -g forms.
    for flags in net.flag_variants(own['toolchain'], list(own['flags'])):
        if list(flags) not in choices:
            choices.append(list(flags))
    probes = [(flags, fn['best_source']) for flags in choices]
    if dict(proj).get('arch') == 'mips' and own['toolchain'].startswith('ido-'):
        from ..arch.mips_runtime import candidate
        if source := candidate(dict(fn).get('asm', ''), fn['name']):
            ranked = prioritized(proj, fn, own)
            probes.insert(1, (ranked[0] if ranked else list(own['flags']), source))
    probes = probes[:max(1, min(MAX_PROFILES, max_profiles))]
    candidates = [(own['toolchain'], tuple(flags)) for flags, _ in probes]
    digest = net._comparison_digest(proj, fn, fn['best_source'], candidates)
    key = 'matching-preflight-v2:'+digest+':'+hashlib.sha256(repr(probes).encode()).hexdigest()
    old = net.store.one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key)
    if old:
        result = json.loads(old['feedback'])
        result = {**result, 'tested': 0, 'cached': True}
    else:
        result = {'tested': 0, 'best': None}
        def ensure():
            if check() is False:
                raise PreflightStopped()
        try:
            with compiler.cancellable(ensure):
                for (toolchain, flags), (_, source) in zip(candidates, probes):
                    ensure()
                    exact, diff, problems = net._judge_with(proj, fn, toolchain, list(flags), source)
                    result['tested'] += 1
                    blocked = unresolved([problems])
                    if blocked:
                        from .helper_call_diagnostics import suggestions
                        for hint in suggestions(getattr(diff, 'rows', [])):log(hint)
                        result['link'] = blocked
                        break
                    if diff is not None:
                        score = 0 if exact else max(1, diff.score)
                        if result['best'] is None or score < result['best']['score']:
                            result['best'] = {'toolchain': toolchain, 'flags': list(flags), 'score': score, 'exact': bool(exact), 'source': source}
                    if exact:
                        break  # Do not spend more budget once the exact judge succeeds.
        except PreflightStopped:
            return result
        if check() is False:
            return result  # Interrupted runs never consume a future retry.
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                       (fn['id'], key, 'preflight', 'bounded compiler preflight', json.dumps(result)))
    if result.get('link'):
        log(result['link']['message'])
    best = result.get('best')
    if best and (best['flags'] != own['flags'] or best.get('source', fn['best_source']) != fn['best_source']) and best['score'] < (fn['best_score'] if fn['best_score'] is not None else float('inf')):
        fresh = net.project_row(proj['slug'])
        current = net._function(fn['name'], proj['slug'])
        # Another worker may have improved the C during probing. Never apply an old-source hypothesis.
        if current['status'] == 'open' and current['best_source'] == fn['best_source']:
            if best['exact'] and net.auto_adjusts(fresh) and best.get('source', fn['best_source']) == fn['best_source']:
                try:
                    net.apply_function_compiler(proj['slug'], fn['name'], best['toolchain'], best['flags'])
                    result['applied'] = True
                    log('Compiler preflight applied a reverified improvement to '+fn['name']+' only.')
                except NetworkError as error:
                    log('Compiler preflight left settings unchanged: '+str(error))
            else:
                if not result.get('cached'):
                    agent, _ = net.local_agent('autopilot')
                    try:
                        net.suggest(agent, proj['slug'], 'Compiler preflight found a rejudged improvement.',
                                    compiler=best['toolchain'], flags=' '.join(best['flags']), function=fn['name'],
                                    ranges=f"0x{fn['vram']:X}-0x{fn['vram']+fn['size']:X}", source=best.get('source', fn['best_source']))
                    except NetworkError as error:
                        # A partial/stale hypothesis may fail the exact suggestion gate. It must not
                        # abort source search or apply settings without the normal revalidation.
                        if not str(error).startswith('not suggested:'):raise
                        result['suggestion_rejected'] = str(error)
                        log('Compiler preflight kept the working settings and will continue source search: '+str(error))
                log('Compiler preflight found a better source/profile hypothesis: '+' '.join(best['flags'])+
                    '. Partial results need review; exact results also respect automatic compiler settings.')
    return result
