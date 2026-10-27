"""Try a few local repairs after an agent releases its lease, before another agent edits the draft."""
import logging
import threading

from .permute import Permuter, State

log = logging.getLogger('decompdecomp')


def run(net, slug, fn):
    if slug in net.links or fn['status'] != 'open' or not fn['best_source'] or not fn['best_score']:
        return ''
    state = State(stop=threading.Event())
    agent, _ = net.local_agent('permuter')
    try:
        # _hold rechecks the lease; another agent winning the race simply skips this pass.
        Permuter(net)._repair_round(agent,net.project_row(slug),state,function_id=fn['id'],budget=6,seconds=8)
    except Exception:
        log.exception('Local repair after release failed for %s',fn['name'])
        return '\nLocal repair could not finish; your saved candidate and release are retained.'
    if not state.repairs:
        return ''
    current = net._function(fn['name'],slug)
    outcome = 'MATCH! verified by the normal judge' if current['status']=='matched' else f"best score {current['best_score']}"
    return f'\nAutomatic local repair: {state.repairs} trials without an agent turn; {outcome}. Next claim starts from the best saved source.'
