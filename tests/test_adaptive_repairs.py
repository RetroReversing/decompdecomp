"""Outcome histories alter priorities without disabling exploration or crossing build profiles."""
from decompnet.app import adaptive_repairs as policy
from tests.test_permute import FakeNet


def test_sparse_history_does_not_reweight_and_measured_failures_never_disable_a_family():
    net = FakeNet()
    for _ in range(31):
        policy.record(net, 'profile', 'register', 'bad', 20, None)
    assert policy.factors(net, 'profile', 'register') == {}
    for _ in range(200):
        policy.record(net, 'profile', 'register', 'bad', 20, None)
    assert policy.MIN_FACTOR <= policy.factors(net, 'profile', 'register')['bad'] < 1
    assert policy.factors(net, 'other compiler', 'register') == {}
    assert policy.factors(net, 'profile', 'call') == {}


def test_verified_wins_receive_more_weight_and_policy_survives_a_new_reader():
    net = FakeNet()
    for _ in range(32):
        policy.record(net, 'profile', 'control', 'useful', 20, 0, 10)
    assert 1 < policy.factors(net, 'profile', 'control')['useful'] <= 4
    net._repair_policy_ready = False
    assert policy.factors(net, 'profile', 'control')['useful'] > 1
    row = net.store.one('SELECT * FROM repair_policy')
    assert row['attempted'] == row['matched'] == row['improved'] == 32
    assert row['elapsed_ms'] == 320


def test_compiler_flags_are_part_of_the_policy_profile(monkeypatch):
    net = FakeNet()
    proj, fn = net.project_row('game'), net.function()
    first = policy.profile(net, proj, fn)
    original = net.cfg(proj)
    monkeypatch.setattr(net, 'cfg', lambda p: {**original, 'compiler':{**original['compiler'], 'flags':['-O3']}})
    assert policy.profile(net, proj, fn) != first


def test_adaptive_family_order_interleaves_original_exploration(monkeypatch):
    net = FakeNet()
    proj, fn = net.project_row('game'), net.function()
    monkeypatch.setattr(policy, 'factors', lambda *a: {'useful':4,'bad':0.25})
    assert policy.order(net, proj, fn, [], ['bad','unknown','useful']) == ['useful','bad','unknown']
