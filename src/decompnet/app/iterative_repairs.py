"""A persistent, bounded beam of local repairs; only the normal judge accepts matches."""

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from itertools import zip_longest
from pathlib import Path

from ..network import NetworkError
from ..project import compiler_for
from . import region_repairs, workers

WIDTH, DEPTH, PER_FUNCTION = 3, 3, 8
DETOUR_RATIO = 1.25


def advance(state, evaluate, variants, stopped):
    """Resume a bounded beam with one reserved slot for a temporarily worse source shape.

    Detours spend the same phase budget and never replace saved best C. A later repair may need
    an address or scope shape that initially makes register allocation worse.
    """
    while state['pending'] and not stopped():
        node = state['pending'][0]
        report = evaluate(node['source'])
        if report is None:  # busy work stays retryable
            return False
        state['pending'].pop(0)
        if report.get('matched'):
            return True
        score = report.get('score')
        if isinstance(score, (int, float)) and 0 < score <= node['ceiling'] * DETOUR_RATIO:
            # Keep two source shapes per identical output: a later source edit can distinguish them.
            # This only prunes search branches, never supplies a match verdict.
            output = output_key(report)
            outputs = state.setdefault('outputs', {})
            if output is None or outputs.get(output, 0) < 2:
                regions = region_repairs.snapshot(report.get('rows'))
                state['next'].append({**node, 'score': score, 'report': report, 'regions': regions,
                                      'region_gain': region_repairs.gain(node.get('region_baseline'), regions)})
                if output is not None:
                    outputs[output] = outputs.get(output, 0) + 1
        if not state['pending']:
            ranked = sorted(state['next'], key=lambda n: n['score'])
            ordinary = [n for n in ranked if n['score'] <= n['ceiling']]
            detours = sorted((n for n in ranked if n['score'] > n['ceiling']),
                             key=lambda n: (-n.get('region_gain', 0), n['score']))
            best = ordinary[:WIDTH-1] + detours[:1] if detours else ordinary[:WIDTH]
            state['next'] = []
            for parent in best:
                if parent['depth'] >= DEPTH:
                    continue
                for source in variants(parent['source'], parent['report']):
                    if source in state['seen']:
                        continue
                    state['seen'].append(source)
                    state['pending'].append(dict(source=source, depth=parent['depth'] + 1,
                                                 ceiling=min(parent['ceiling'], parent['score']),
                                                 region_baseline=parent.get('regions')))
    return False


def repair_rules():
    from ..claim_stalls import file_digest
    from ..claim_stalls import repair_rules as rules
    from . import repair_ranking
    return rules() + [('repair_ranking', file_digest(Path(repair_ranking.__file__)))]


def output_key(report):
    if report.get('candidate_target') is None:
        return None  # displayed rows alone are insufficient evidence of identical compiler output
    return hashlib.sha256(repr((report['candidate_target'], report.get('candidate_relocs'))).encode()).hexdigest()


def targeted_variants(source, report, fn, arch, flags, header=''):
    from . import draft_repairs, repair_ranking, stack_padding_repairs
    labels = {}
    proposals = draft_repairs.variants(source, fn['name'], arch, fn['asm'] or '', flags, labels, header=header)
    for text in stack_padding_repairs.grow(source, fn['name'], arch, report.get('rows') or []):
        proposals.append(text)
        labels[text] = 'stack_padding'
    families = repair_ranking.order(list(dict.fromkeys(labels[p] for p in proposals)), report.get('rows') or [])
    return sorted(proposals, key=lambda p: families.index(labels[p]))


def run(twins, slug, job):
    """Use a separate phase budget and resume after the last visited function."""
    from . import address_repairs, free_candidates, similar_matches

    net = twins.net
    proj = net.project_row(slug)
    # The current proposals use MIPS linked feedback; object projects need file-context adaptation.
    if not net.is_rom(proj) or proj['arch'] != 'mips' or net.fork_slug(slug) or slug in net.links:
        return 0
    free_candidates.budget(job)
    limit, deadline = job.reuse_limit, job.reuse_deadline
    agent, _ = net.local_agent('autopilot')
    cfg = net.cfg(proj)
    symbols = [(r['name'], r['address']) for r in net.store.all(
        'SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    known = {name for name, _ in symbols}
    matched = [0]
    rules = repair_rules()
    rows = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
                         "AND best_score>0 AND best_score<=?*MAX(1,n_insns) "
                         "ORDER BY id", proj['id'], 100 - similar_matches.REPAIR_PERCENT)
    cursor = net.store.one('SELECT function_id FROM repair_cursors WHERE project_id=?', proj['id'])
    last = cursor['function_id'] if cursor else 0
    rows.sort(key=lambda fn: (fn['id'] <= last, fn['id']))
    guard = threading.Lock()  # (the job's counters are shared by every thread's function)

    def visit(fn):
        if free_candidates.exhausted(job) or job.reuse_attempts >= limit or time.monotonic() >= deadline:
            return
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO repair_cursors VALUES(?,?)', (proj['id'], fn['id']))
        if net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id']):
            return
        header = net._context(proj['id'], fn['unit'])['header']
        settings = compiler_for(cfg, fn['vram'])
        context = similar_matches.trial_key('', header, settings, fn, ['beam-v4', symbols, rules])
        saved = net.store.one('SELECT context,state FROM repair_frontiers WHERE function_id=?', fn['id'])
        state = json.loads(saved['state']) if saved and saved['context'] == context else None
        if state is None or fn['best_source'] not in state['seen']:
            state = dict(pending=[dict(source=fn['best_source'], depth=0, ceiling=fn['best_score'])],
                         next=[], seen=[fn['best_source']])
        tries = [0]  # this function's own attempts: other threads are spending the job's at the same time

        def stopped():
            return (free_candidates.exhausted(job) or job.reuse_attempts >= limit or tries[0] >= PER_FUNCTION
                    or time.monotonic() >= deadline)

        def evaluate(source):
            # Reuse the ordinary free-search cache, including old seed probes.
            for kind in ('address-repair', 'address-probe'):
                key = similar_matches.trial_key(source, header, settings, fn, [kind, symbols])
                cached = net.store.one("SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=? AND outcome='miss'",
                                       fn['id'], key)
                if cached:
                    return json.loads(cached['feedback'])
            key = similar_matches.trial_key(source, header, settings, fn, ['address-repair', symbols])
            with guard:
                job.reuse_attempts += 1
            tries[0] += 1
            try:
                report = net.try_quietly(agent, proj, fn, source)
            except NetworkError:
                return None
            outcome = 'match' if report.get('matched') else 'miss'
            reason = 'iterative local repair; ' + ('byte match' if outcome == 'match' else f"score {report.get('score')}")
            with net.store.tx() as db:
                db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                           (fn['id'], key, outcome, reason, json.dumps(report)))
                db.execute('INSERT INTO free_match_attempts(project_id,function_id,strategy,outcome,reason,created_at) VALUES(?,?,?,?,?,?)',
                           (proj['id'], fn['id'], 'iterative-repair', outcome, reason, time.time()))
            return report

        def variants(source, report):
            guided = address_repairs.variants(source, report, fn, bool(proj['big_endian']), known)
            if report.get('candidate_target'):
                compiled = dict(target=bytes.fromhex(report['candidate_target']), relocs=report.get('candidate_relocs', '[]'))
                guided += similar_matches.literal_variants(source, compiled, fn, bool(proj['big_endian']))
            targeted = targeted_variants(source, report, fn, proj['arch'], settings.get('flags', []), header)
            # Interleave pools so address proposals cannot starve targeted families (or vice versa).
            pools = [guided[:4], targeted[:8], similar_matches.repair_variants(source, report)[:4]]
            return list(dict.fromkeys(item for group in zip_longest(*pools)
                                      for item in group if item is not None))

        if advance(state, evaluate, variants, stopped):
            with guard:
                matched[0] += 1
                job.reuse_matched += 1
                job.reuse_bytes += fn['size']
            net.add_note(agent, slug, 'Autopilot matched with iterative local repairs', fn['name'])
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO repair_frontiers VALUES(?,?,?)', (fn['id'], context, json.dumps(state)))

    # One thread to a function at a time (Settings: "Iterative repairs: threads"), on a pool of their own: this
    # runs on one of the shared workers, which would wait for the very threads it is waiting on.
    threads = workers.clamp(getattr(getattr(net, 'cascades', None), 'iterative_threads', 0))
    with ThreadPoolExecutor(threads, thread_name_prefix='iterative-repair', initializer=workers.lower_priority) as pool:
        list(pool.map(visit, rows))
    return matched[0]


def optional_phase(twins, slug, parent):
    """Opt-in after normal autopilot; background cascades keep their original budget and behaviour."""
    from . import free_candidates
    from .twins import Job

    config = twins.net.cascades
    if not config.iterative_enabled or parent.stop.is_set():
        return 0
    phase = Job(stop=parent.stop)
    free_candidates.budget(phase, config.iterative_seconds, config.iterative_attempts)
    started = time.monotonic()
    matched = run(twins, slug, phase)
    net = twins.net
    proj = net.project_row(slug)
    with net.store.tx() as db:
        db.execute('INSERT INTO iterative_runs(project_id,attempts,matched,bytes,seconds,created_at) VALUES(?,?,?,?,?,?)',
                   (proj['id'], phase.reuse_attempts, phase.reuse_matched, phase.reuse_bytes,
                    time.monotonic() - started, time.time()))
    agent, _ = net.local_agent('autopilot')
    net.add_note(agent, slug, f'Iterative search: {matched} matches, {phase.reuse_bytes} bytes, '
                 f'{phase.reuse_attempts} attempts (separate budget).')
    return matched
