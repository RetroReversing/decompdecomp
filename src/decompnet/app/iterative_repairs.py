"""A persistent, bounded beam of local repairs; only the normal judge accepts matches."""

import json
import time

from ..network import NetworkError
from ..project import compiler_for

WIDTH, DEPTH, PER_FUNCTION = 3, 3, 8


def advance(state, evaluate, variants, stopped):
    """Resume a breadth-first beam, retaining improvements and bounded equal-score alternatives."""
    while state['pending'] and not stopped():
        node = state['pending'][0]
        report = evaluate(node['source'])
        if report is None:  # busy work stays retryable
            return False
        state['pending'].pop(0)
        if report.get('matched'):
            return True
        score = report.get('score')
        if isinstance(score, (int, float)) and 0 < score <= node['ceiling']:
            state['next'].append({**node, 'score': score, 'report': report})
        if not state['pending']:
            best = sorted(state['next'], key=lambda n: n['score'])[:WIDTH]
            state['next'] = []
            for parent in best:
                if parent['depth'] >= DEPTH:
                    continue
                for source in variants(parent['source'], parent['report']):
                    if source in state['seen']:
                        continue
                    state['seen'].append(source)
                    state['pending'].append(dict(source=source, depth=parent['depth'] + 1,
                                                 ceiling=parent['score']))
    return False


def run(twins, slug, job):
    """Use at most a quarter of this wave for saved near misses, leaving room for transfers/templates."""
    from . import address_repairs, free_candidates, similar_matches

    net = twins.net
    proj = net.project_row(slug)
    # The current proposals use MIPS linked feedback; object projects need file-context adaptation.
    if not net.is_rom(proj) or proj['arch'] != 'mips' or net.fork_slug(slug) or slug in net.links:
        return 0
    free_candidates.budget(job)
    if not hasattr(job, 'repair_limit'):
        job.repair_limit = job.reuse_attempts + max(0, (job.reuse_limit - job.reuse_attempts) // 4)
        job.repair_deadline = time.monotonic() + max(0, job.reuse_deadline - time.monotonic()) / 4
    limit, deadline = job.repair_limit, job.repair_deadline
    agent, _ = net.local_agent('autopilot')
    cfg = net.cfg(proj)
    symbols = [(r['name'], r['address']) for r in net.store.all(
        'SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    known = {name for name, _ in symbols}
    matched = 0
    rows = net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
                         "AND best_score>0 AND best_score<=?*MAX(1,n_insns) "
                         "ORDER BY best_score*1.0/MAX(1,n_insns),size,id", proj['id'], 100 - similar_matches.REPAIR_PERCENT)
    for fn in rows:
        if free_candidates.exhausted(job) or job.reuse_attempts >= limit or time.monotonic() >= deadline:
            break
        if net.store.one('SELECT 1 FROM claims WHERE function_id=?', fn['id']):
            continue
        header = net._context(proj['id'], fn['unit'])['header']
        settings = compiler_for(cfg, fn['vram'])
        context = similar_matches.trial_key('', header, settings, fn, ['beam-v1', symbols])
        saved = net.store.one('SELECT context,state FROM repair_frontiers WHERE function_id=?', fn['id'])
        state = json.loads(saved['state']) if saved and saved['context'] == context else None
        if state is None or fn['best_source'] not in state['seen']:
            state = dict(pending=[dict(source=fn['best_source'], depth=0, ceiling=fn['best_score'])],
                         next=[], seen=[fn['best_source']])
        start = job.reuse_attempts

        def stopped(start=start):
            return (free_candidates.exhausted(job) or job.reuse_attempts >= min(limit, start + PER_FUNCTION)
                    or time.monotonic() >= deadline)

        def evaluate(source, fn=fn, header=header, settings=settings):
            # Reuse the ordinary free-search cache, including old seed probes.
            for kind in ('address-repair', 'address-probe'):
                key = similar_matches.trial_key(source, header, settings, fn, [kind, symbols])
                cached = net.store.one("SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=? AND outcome='miss'",
                                       fn['id'], key)
                if cached:
                    return json.loads(cached['feedback'])
            key = similar_matches.trial_key(source, header, settings, fn, ['address-repair', symbols])
            job.reuse_attempts += 1
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

        def variants(source, report, fn=fn):
            guided = address_repairs.variants(source, report, fn, bool(proj['big_endian']), known)
            if report.get('candidate_target'):
                compiled = dict(target=bytes.fromhex(report['candidate_target']), relocs=report.get('candidate_relocs', '[]'))
                guided += similar_matches.literal_variants(source, compiled, fn, bool(proj['big_endian']))
            # Reserve some proposals for casts/order/register repairs even when address evidence is plentiful.
            return list(dict.fromkeys(guided[:4] + similar_matches.repair_variants(source, report)[:4]))

        if advance(state, evaluate, variants, stopped):
            matched += 1
            job.reuse_matched += 1
            job.reuse_bytes += fn['size']
            net.add_note(agent, slug, 'Autopilot matched with iterative local repairs', fn['name'])
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO repair_frontiers VALUES(?,?,?)', (fn['id'], context, json.dumps(state)))
    return matched
