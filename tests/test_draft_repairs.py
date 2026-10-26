"""Targeted candidates and their independent, cached background budget."""

import threading

import pytest

from decompnet.app.draft_repairs import variants
from decompnet.app.permute import State
from tests.test_permute import AGENT, FakeNet, ScriptedPermuter


def test_address_conversion_and_commutation_are_combined():
    src = 'int f(int i) { return *(int *)((u8 *)table + i); }'
    assert variants(src, 'f', 'mips') == ['int f(int i) { return *(int *)(i + (int)table); }']
    assert not variants(src, 'f', 'x86_64')
    assert not variants(src, 'f', 'unknown')


@pytest.mark.parametrize('arch', ['mips', 'ppc', 'x86', 'x86_64', 'arm', 'thumb', 'unknown'])
def test_early_return_is_shared_and_keeps_expression(arch):
    src = 'int f(int x) { int result; result = 0; if (x >= 0) { result = (int)root((float)x); } return result; }'
    outputs = variants(src, 'f', arch)
    assert any('if (x < 0) return 0;' in out and 'return (int)root((float)x);' in out for out in outputs)


def test_nested_call_argument_boundary_and_opaque_text():
    src = 'int f(int x) { /* other(x, 123) */ return send(inner(x, 2), 0x40); }'
    out = variants(src, 'f', 'mips')
    assert 'return send(inner(x, 2));' in out[0]
    assert '/* other(x, 123) */' in out[0]
    assert all('other(x, 123)' in v for v in out)
    assert not variants('int f(int x) { return send(x, side_effect()); }', 'f', 'mips')
    assert not variants('int f(int x) { return send(x, "abc"); }', 'f', 'mips')


def test_only_requested_definition_and_no_directives():
    src = 'int other(int x) { return send(x, 7); }\nint f(int x) { return x; }'
    assert not variants(src, 'f', 'mips')
    assert not variants('int f(int x) {\n#define GO send(x, 7)\nreturn GO; }', 'f', 'mips')
    assert not variants('int f(int x);', 'f', 'mips')
    # Extra statements and references to the accumulator must not be discarded.
    for expr in ('result + x', 'x'):
        src = f'int f(int x) {{ int result; result=0; side(); if(x>=0){{result={expr};}} return result; }}'
        assert all('side();' in out and f'result={expr};' in out for out in variants(src, 'f', 'mips'))


def setup_repair_net(count=1):
    net = FakeNet()
    with net.store.tx() as db:
        db.execute("DELETE FROM functions")
        for i in range(1, count + 1):
            db.execute('INSERT INTO functions(id,project_id,name,n_insns,best_score,best_source) VALUES(?,1,?,10,200,?)',
                       (i, f'f{i}', f'int f{i}(int x) {{ return send(x, 7); }}'))
    return net


def test_outside_cutoff_cached_and_does_not_spend_random_turns():
    net = setup_repair_net()
    perm = ScriptedPermuter(net, {})
    proj = net.project_row('game')
    assert perm.candidates(proj) == []
    state = State()
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == state.repairs == 1
    assert net.function()['permuted'] == 0
    assert not net.store.all('SELECT * FROM claims')
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 1
    # Changes in the actual shared header invalidate the cache.
    net._context = lambda *args: {'header': 'typedef int word;', 'version': 2}
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 2


def test_rotation_claims_stop_and_exact_match_counts():
    net = setup_repair_net(21)
    perm = ScriptedPermuter(net, {'int f21(int x) { return send(x); }': 0})
    proj = net.project_row('game')
    state = State()
    with net.store.tx() as db:
        db.execute('INSERT INTO claims VALUES(1,1,0,9999999999)')
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 20
    assert all('int f1(' not in s for s in perm.judged)
    assert state.matched == 1
    state.stop.set()
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 20


def test_project_mode_uses_project_header_and_judge():
    net = setup_repair_net()
    net.fork_slug = lambda slug: 'fork'

    class Fork:
        def permuter_header(self, slug, function):
            assert (slug, function) == ('fork', 'f1')
            return 'typedef int word;'

    net.forks = Fork()
    perm = ScriptedPermuter(net, {})
    perm._repair_round(AGENT, net.project_row('game'), State())
    assert len(perm.judged) == 1


def test_compile_budget_rotates_to_later_functions():
    net = setup_repair_net(25)
    with net.store.tx() as db:
        for i in range(1, 26):
            db.execute('UPDATE functions SET best_source=? WHERE id=?',
                       (f'int f{i}(int x) {{ send(x, 7); return send(x, 9); }}', i))
    perm = ScriptedPermuter(net, {})
    state = State()
    proj = net.project_row('game')
    perm._repair_round(AGENT, proj, state)
    assert len(perm.judged) == 32
    perm._repair_round(AGENT, proj, state)
    assert perm.judged[32].startswith('int f17(')


def test_failed_judge_releases_claim_without_caching():
    net = setup_repair_net()
    perm = ScriptedPermuter(net, {})

    def fail(*args):
        raise RuntimeError('compiler interrupted')

    perm._score = fail
    perm._repair_round(AGENT, net.project_row('game'), State())
    assert not net.store.all('SELECT * FROM claims')
    assert not net.store.all('SELECT * FROM reuse_trials')


def test_run_again_does_not_start_random_permutation_or_remote_room_work():
    net = setup_repair_net()
    net.local_agent = lambda name: (AGENT, None)
    perm = ScriptedPermuter(net, {})
    state = perm.repair_pass('game', threading.Event())
    assert state.repairs == 1
    assert net.function()['permuted'] == 0
    net.links = {'game': object()}
    state = perm.repair_pass('game', threading.Event())
    assert state.repairs == 0


@pytest.mark.parametrize('pointer', ['obj->base', 'obj.data.base', 'obj -> link -> base'])
@pytest.mark.parametrize('wrapped', [False, True])
def test_member_address_cast_with_optional_parentheses(pointer, wrapped):
    cast = f'(u8 *) {pointer}'
    if wrapped:
        cast = f'({cast})'
    src = f'int f(int i) {{ return *(int *)({cast} + i); }}'
    assert f'int f(int i) {{ return *(int *)(i + (int){pointer}); }}' in variants(src, 'f', 'mips')
    assert not variants(src, 'f', 'x86_64')


def test_member_rule_excludes_calls_and_increments():
    for expr in ['get()->base', 'obj[i++].base', 'obj->base++']:
        src = f'int f(int i) {{ return *(int *)(((u8 *) {expr}) + i); }}'
        assert all('side();' in out and f'result={expr};' in out for out in variants(src, 'f', 'mips'))
