"""Repeated-record recipes reach both search modes under ordinary budgets and judging."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from decompnet.app import deep_permuter, iterative_repairs
from decompnet.app.permute import Permuter
from decomprepair import indexed_record_repairs
from tests.test_deep_permuter import Log, setup


def test_record_recipe_pages_resume_then_return_to_ordinary_proposals(monkeypatch):
    monkeypatch.setattr(indexed_record_repairs, 'sequences', lambda *a: [('indexed_record_sequence', s) for s in ['a', 'b', 'c']])
    monkeypatch.setattr(iterative_repairs, 'targeted_pool', lambda *a, **k: [('ordinary', 'other')])
    state = {}
    arguments = ('seed', {}, {'name': 'f', 'asm': ''}, 'mips', [])
    first = iterative_repairs.targeted_variants(*arguments, tagged=True, selection=state, limit=2)
    second = iterative_repairs.targeted_variants(*arguments, tagged=True, selection=state, limit=2)
    third = iterative_repairs.targeted_variants(*arguments, tagged=True, selection=state, limit=2)
    assert [s for _, s in first] == ['a', 'b'] and first.more
    assert [s for _, s in second] == ['c'] and second.more
    assert [s for _, s in third] == ['other']
    omitted = iterative_repairs.targeted_variants(*arguments, tagged=True, selection={}, limit=2,
                                                omit_families=('indexed_record_sequence',))
    assert [s for _, s in omitted] == ['other']


def test_record_implementation_changes_invalidate_searches_and_replace_workers():
    from decompnet import claim_stalls
    from decompnet.app import permuter_worker

    rules = dict(claim_stalls.repair_rules())
    assert rules['indexed_record_repairs.py'] != 'unavailable'
    assert rules['indexed_record_support.py'] != 'unavailable'
    watched = {Path(name).name for name, stamp, size in permuter_worker.revision() if stamp is not None}
    assert {'indexed_record_repairs.py', 'indexed_record_support.py'} <= watched


@pytest.mark.parametrize('imported', [False, True])
def test_deep_record_recipe_uses_the_selected_mode_judge_and_stops_at_its_budget(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    monkeypatch.setattr(indexed_record_repairs, 'sequences', lambda *a: [('indexed_record_sequence', 'first'), ('indexed_record_sequence', 'second')])
    seen = []
    def judge(self, agent, proj, fn, source, best, fork):
        seen.append((source, fork))
        return 30
    monkeypatch.setattr(Permuter, '_score', judge)
    result = deep_permuter.run(net, 'game', 'f', 1, 1, Log())
    assert seen == [('first', 'fork' if imported else None)]
    assert result['tested'] == 1 and not result['matched']
    assert result['reason'] == 'Variant limit reached'
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_private_original_record_candidate_reaches_its_frozen_score_without_intermediates():
    location = os.environ.get('DECOMPNET_INDEXED_RECORD_REPLAY')
    if not location:
        pytest.skip('Supply copied private inputs; game source and bytes never enter CI.')
    from decompnet.app import linked_repair_benchmark as bench
    folder = Path(location)
    manifest = json.loads((folder/'inputs.json').read_text())
    source, header = (folder/'original.c').read_text(), (folder/'context.h').read_text()
    assert hashlib.sha256(source.encode()).hexdigest() == manifest['original.c']
    assert hashlib.sha256(header.encode()).hexdigest() == manifest['context.h']
    # The manual result is deliberately not read or supplied to the search.
    result = bench.suite(folder/'project', [dict(name=manifest['function'], source=source, header=header)],
                         trials=manifest['trials'], seconds=manifest['seconds'])
    case = result['cases'][0]
    assert case['baseline_score'] == manifest['baseline_score'] and not case['baseline_exact']
    assert case['best_score'] <= manifest['maximum_score']
    assert result['candidate_compiles'] <= manifest['trials']
    assert case['first_exact_trial'] is None  # A recovered near match remains a near match.
    (folder/'integrated-replay.json').write_text(json.dumps(result, indent=2)+'\n')
