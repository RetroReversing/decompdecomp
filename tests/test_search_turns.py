from decompnet.app.deep_permuter import Search
from decompnet.app.search_turns import Allowance
from tests.test_deep_permuter import Log
from tests.test_permute import FakeNet


def test_productive_turns_extend_only_within_the_requested_hard_cap():
    budget=Allowance(120,0)
    assert not budget.reason(59)
    budget.observe(True,'code',59)
    assert budget.soft==119 and not budget.reason(100)
    budget.observe(True,'new-code',110)
    assert budget.soft==120 and budget.reason(120).startswith('Budget rotation:')


def test_identical_output_rotates_but_diverse_output_and_recent_improvements_do_not():
    budget=Allowance(120,0)
    for _ in range(32):budget.observe(False,'same',10)
    assert not budget.reason(14)
    assert 'repeated function output' in budget.reason(20)
    budget.observe(True,'improved',21)
    assert not budget.reason(21)
    for i in range(32):budget.observe(False,str(i),22)
    assert not budget.reason(22)


def test_explicit_single_function_search_retains_its_full_budget():
    net=FakeNet();log=Log()
    assert Search(net,2,100,log,net.function()).allowance is None
    log.adaptive_budget=True
    assert Search(net,2,100,log,net.function()).allowance is not None
