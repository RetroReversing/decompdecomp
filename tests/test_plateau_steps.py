"""A structural repair that comes out level with its parent gets one burst of follow-ups from its own C: two steps an
N64 function needed together, where the first alone scored the same (or a little worse) and was dropped."""
from decompnet.app import deep_permuter, draft_repairs, plateau_steps
from decompnet.app.permute import Permuter
from tests.test_deep_permuter import Log, setup


def test_a_level_structural_step_is_a_plateau_only_when_it_changed_the_code():
    assert plateau_steps.plateau('symbol_address_local', 461, 461)
    assert plateau_steps.plateau('target_call_arity', 461, 471)  # 2% behind
    assert not plateau_steps.plateau('target_call_arity', 461, 520)  # too far behind
    assert not plateau_steps.plateau('target_call_arity', 461, 400)  # an improvement takes the normal path
    assert not plateau_steps.plateau('local_declaration_order', 461, 461)  # not a structural rewrite
    assert not plateau_steps.plateau('symbol_address_local', 461, 461, 'same', 'same')  # identical output


def test_follow_ups_put_other_structural_rewrites_first_and_skip_seen_ones():
    proposals = [('local_declaration_order', 'a'), ('symbol_address_local', 'b'), ('goto_backward_loop', 'c'),
                 ('goto_backward_loop', 'd')]
    chosen = plateau_steps.select(proposals, 'symbol_address_local', seen={'d'})
    assert [c for _, c in chosen] == ['c', 'b', 'a']


def run(monkeypatch, enabled):
    net = setup(monkeypatch)
    saved = net.function()['best_source']
    tried = []

    def variants(source, *args, labels=None, **kwargs):
        labels = labels if labels is not None else (args[4] if len(args) > 4 else {})
        # (a real queue holds hundreds of proposals: the budget ends long before it does, and so before any detour)
        others = [('local_declaration_order', f'other {i}') for i in range(20)]
        offered = {saved: [('symbol_address_local', 'A')] + others, 'A': [('goto_backward_loop', 'B')]}.get(source, [])
        labels.update({c: f for f, c in offered})
        return [c for _, c in offered]

    def judge(self, agent, proj, fn, source, best, fork):
        tried.append(source)
        score = {'A': 40, 'B': 0}.get(source, 100)
        if score < best:
            with net.store.tx() as db:
                db.execute('UPDATE functions SET best_score=?,best_source=?,status=? WHERE id=?',
                           (score, source, 'matched' if score == 0 else 'open', fn['id']))
        return score
    monkeypatch.setattr(draft_repairs, 'variants', variants)
    monkeypatch.setattr(Permuter, '_score', judge)
    if not enabled:
        monkeypatch.setattr(plateau_steps, 'BURSTS', 0)
    deep_permuter.run(net, 'game', 'f', 1, 6, Log())
    return net.function()['status'], tried


def test_the_second_step_is_reached_and_judged_normally(monkeypatch):
    # (the saved best scores 40; `A` comes out level at 40 and alone changes nothing that's kept)
    status, tried = run(monkeypatch, True)
    assert status == 'matched' and tried.index('A') < tried.index('B')
    assert tried.index('B') == tried.index('A') + 1  # queued first, ahead of the other proposals


def test_without_the_burst_the_pass_stops_at_the_level_step(monkeypatch):
    status, tried = run(monkeypatch, False)
    assert status == 'open' and 'B' not in tried
