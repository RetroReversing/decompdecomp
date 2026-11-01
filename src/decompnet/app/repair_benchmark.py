"""Bounded, multi-step frozen-output replay using the autopilot beam, without adopting game C."""
import hashlib
import time
from collections import Counter
from itertools import zip_longest

from . import iterative_repairs


def run(source, baseline_score, evaluate, generate, *, trials, deadline, check_cancel=lambda: None,
        progress=lambda value: None):
    """Measure rediscovery effort, including failures; evaluator must supply its exact control verdict."""
    started = time.monotonic()
    state = dict(pending=[dict(source=source, depth=0, ceiling=baseline_score)], next=[], seen=[source])
    results = []
    check_cancel()
    baseline = evaluate(source)

    def judge(candidate):
        check_cancel()
        # The baseline is already compiled by replay, and callers pass its frozen report.
        report = baseline if candidate == source else evaluate(candidate)
        check_cancel()
        if candidate != source:
            node = state['pending'][0]
            row = {'path': node.get('path', []), 'family': node.get('path', ['baseline'])[-1],
                   'source_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
                   'outcome': report['outcome'], 'score': report.get('score')}
            results.append(row)
            progress({'tested': len(results), 'budget': trials, **row})
        return report

    def variants(parent, report):
        pools = {}
        for family, candidate in generate(parent):
            pools.setdefault(family, []).append((family, candidate))
        return [item for row in zip_longest(*pools.values()) for item in row if item is not None]

    def stopped():
        check_cancel()
        return len(results) >= trials or time.monotonic() >= deadline
    matched = iterative_repairs.advance(state, judge, variants, stopped)
    reason = ('control_output_recovered' if matched else 'time_budget' if time.monotonic() >= deadline
              else 'trial_budget' if len(results) >= trials and state['pending'] else state['reason'])
    return {'trials': results, 'summary': dict(Counter(r['outcome'] for r in results)),
            'remaining': len(state['pending']), 'reason': reason, 'elapsed_seconds': time.monotonic()-started,
            'candidate_compiles': len(results),
            'first_exact_trial': len(results) if matched else None, 'winning_path': state.get('winning_path'),
            'search_counts': state.get('counts', {})}
