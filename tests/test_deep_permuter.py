import pytest

from decompnet.app import deep_permuter, draft_repairs, permuter_local
from decompnet.app.permute import Permuter
from decompnet.network import NetworkError
from tests.test_permute import AGENT, FakeNet, ProjectStub, batches


class Log:
    def __init__(self):
        self.lines, self.updates = [], []
        self.cancelled = False
    def __call__(self, line):
        self.check_cancel(); self.lines.append(line)
    def check_cancel(self):
        if self.cancelled:
            raise RuntimeError('cancelled')
    def progress(self, value):
        self.check_cancel(); self.updates.append(value)


def setup(monkeypatch, imported=False):
    net = FakeNet()
    monkeypatch.setattr('decompnet.app.compiler_preflight.warnings', lambda *args: '')
    net._function = lambda name, slug: net.function()
    net.local_agent = lambda name: (AGENT, None)
    net.fork_slug = lambda slug: 'fork' if imported else None
    class Fork(ProjectStub):
        def permuter_header(self, slug, name):
            return ''
    net.forks = Fork()
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open'")
    monkeypatch.setattr(deep_permuter.Search, '_baseline', lambda *args: (40, 0))
    monkeypatch.setattr(draft_repairs, 'variants', lambda *args, **kwargs: ['local'])
    monkeypatch.setattr(permuter_local, 'variants', lambda *args: [])
    return net


@pytest.mark.parametrize('imported', [False, True])
def test_one_function_deep_search_uses_normal_judge_and_keeps_improvements(monkeypatch, imported):
    net = setup(monkeypatch, imported)
    seen = []
    def judge(self, agent, proj, fn, source, best, fork):
        seen.append((source, fork))
        score = {'local':20, 'random':0}.get(source, 100)
        if score < best:
            with net.store.tx() as db:
                db.execute('UPDATE functions SET best_score=?,best_source=?,status=? WHERE id=?',
                           (score, source, 'matched' if score == 0 else 'open', fn['id']))
        return score
    monkeypatch.setattr(Permuter, '_score', judge)
    batches(monkeypatch, ['local', 'random', 'never tested'])
    log = Log()
    result = deep_permuter.run(net, 'game', 'f', 30, 10, log)
    assert result['matched'] and result['tested'] == 2 and result['skipped'] == 1
    assert seen == [('local','fork' if imported else None), ('random','fork' if imported else None)]
    assert net.function()['status'] == 'matched'
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0
    assert log.updates[-1]['best_score'] == 0


def test_variant_limit_cancellation_and_duplicate_misses_release_the_lease(monkeypatch):
    net = setup(monkeypatch)
    monkeypatch.setattr(Permuter, '_score', lambda *args: 40)
    batches(monkeypatch, ['different'])
    result = deep_permuter.run(net, 'game', 'f', 1, 1, Log())
    assert result['reason'] == 'Variant limit reached' and result['tested'] == 1
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0
    log = Log()
    def cancel(*args):
        log.cancelled = True
        return 40
    monkeypatch.setattr(Permuter, '_score', cancel)
    # Change the build inputs so the first local candidate is not a reusable old miss.
    net._context = lambda *args: {'header':'changed', 'version':2}
    with pytest.raises(RuntimeError, match='cancelled'):
        deep_permuter.run(net, 'game', 'f', 1, 10, log)
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_failed_compiles_remain_retryable_and_better_cached_results_are_rejudged(monkeypatch):
    net = setup(monkeypatch)
    calls = []
    monkeypatch.setattr(Permuter, '_score', lambda *args: calls.append(1))
    log = Log(); search = deep_permuter.Search(net, 1, 10, log)
    proj, fn = net.project_row('game'), net.function()
    search._score(AGENT, proj, fn, 'bad', 40, None)
    search._score(AGENT, proj, fn, 'bad', 40, None)
    assert len(calls) == 2
    monkeypatch.setattr(Permuter, '_score', lambda *args: calls.append(1) or 20)
    search._score(AGENT, proj, fn, 'ok', 40, None)
    search._score(AGENT, proj, fn, 'ok', 40, None)
    assert len(calls) == 4  # a cache hit cannot substitute for saving/judging an improvement


@pytest.mark.parametrize('minutes,limit', [(0,1), (121,1), (True,1), (1,0), (1,1000001), (1,True)])
def test_invalid_limits_do_not_start_a_search(monkeypatch, minutes, limit):
    net = setup(monkeypatch)
    with pytest.raises(NetworkError):
        deep_permuter.validate(net, 'game', 'f', minutes, limit)


def test_busy_matched_and_unscored_functions_are_rejected(monkeypatch):
    net = setup(monkeypatch)
    for values in ["status='matched'", "best_source=NULL", "best_score=NULL"]:
        with net.store.tx() as db: db.execute('UPDATE functions SET '+values)
        with pytest.raises(NetworkError): deep_permuter.validate(net, 'game', 'f', 1, 10)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',best_source='int f(){return 1;}',best_score=1")
        db.execute('INSERT INTO claims VALUES(1,1,0,9999999999)')
    with pytest.raises(NetworkError, match='working'):
        deep_permuter.validate(net, 'game', 'f', 1, 10)


def test_time_limit_ends_before_a_compile_and_keeps_the_lease_free(monkeypatch):
    net = setup(monkeypatch)
    initialize = deep_permuter.Search.__init__
    def expired(self, *args):
        initialize(self, *args)
        self.deadline = 0
    monkeypatch.setattr(deep_permuter.Search, '__init__', expired)
    result = deep_permuter.run(net, 'game', 'f', 1, 100, Log())
    assert result['reason'] == 'Time limit reached' and result['tested'] == 0
    assert net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_cancelling_random_search_closes_generator_and_releases_function(monkeypatch):
    net = setup(monkeypatch)
    monkeypatch.setattr(draft_repairs, 'variants', lambda *args, **kwargs: [])
    log = Log(); closed = []
    def judge(*args):
        log.cancelled = True
        return 40
    monkeypatch.setattr(Permuter, '_score', judge)
    from decompnet.app import permute
    class Generator:
        def batch(self, job): return ['one']
        def close(self): closed.append(True)
    monkeypatch.setattr(permute, 'available', lambda: True)
    monkeypatch.setattr(permute, 'Generator', Generator)
    with pytest.raises(RuntimeError, match='cancelled'):
        deep_permuter.run(net, 'game', 'f', 1, 100, log)
    assert closed and net.store.one('SELECT COUNT(*) n FROM claims')['n'] == 0


def test_a_scalar_cache_does_not_replace_baseline_instruction_evidence(monkeypatch):
    measure = deep_permuter.Search._baseline
    net = setup(monkeypatch)
    monkeypatch.setattr(deep_permuter.Search, '_baseline', measure)
    seen = []
    monkeypatch.setattr(Permuter, '_score', lambda *args: seen.append(1) or 40)
    def baseline(self, agent, proj, fn, state, fork):
        return self._score(agent, proj, fn, 'cached', 40, fork), 1
    monkeypatch.setattr(Permuter, '_baseline', baseline)
    search = deep_permuter.Search(net, 1, 10, Log())
    proj, fn = net.project_row('game'), net.function()
    search._score(AGENT, proj, fn, 'cached', 40, None)
    search._baseline(AGENT, proj, fn, None, None)
    assert len(seen) == 2


def test_invalid_generated_calls_do_not_consume_the_compile_allowance(monkeypatch):
    net = setup(monkeypatch)
    search = deep_permuter.Search(net, 1, 4, Log())
    calls = []
    monkeypatch.setattr(Permuter, '_score', lambda *args:calls.append(1) or 40)
    net._context = lambda *args: {'header':'', 'version':1}
    source = 'int send(int a,int b); int f(void) { return send(1); }'
    assert search._score(AGENT, net.project_row('game'), net.function(), source, 40, None) is None
    assert search.rejected == 1 and search.tested == 0 and not calls


def test_live_metrics_count_only_verified_strict_improvements_and_keep_starting_baseline(monkeypatch):
    net = setup(monkeypatch)
    fn = net.function()
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    answers = iter([50, 30, 30, None, 20, 20, 0])
    monkeypatch.setattr(Permuter, '_score', lambda *args:next(answers))
    proj = net.project_row('game')
    search.measuring_baseline = True
    search._score(AGENT, proj, fn, 'baseline', 40, None)
    assert search.metrics()['starting_score'] == 50  # The stale saved score was 40.
    search.measuring_baseline = False
    for source in ['better', 'equal', 'error', 'better again']:
        search._score(AGENT, proj, fn, source, 50, None)
    assert search.improvements == 2 and search.best == 20
    # A second baseline when random search starts must not erase local-repair progress.
    search.measuring_baseline = True
    search._score(AGENT, proj, fn, 'current baseline', 20, None)
    search.measuring_baseline = False
    search._score(AGENT, proj, fn, 'match', 20, None)
    assert search.metrics() == {'starting_score':50,'starting_percent':95.0,'best_score':0,
                                'best_percent':100.0,'improvements':3}
    assert search.log.updates[-1]['improvements'] == 3


def test_percentages_do_not_round_a_near_match_to_100(monkeypatch):
    net = setup(monkeypatch)
    fn = {**dict(net.function()), 'n_insns':10000, 'best_score':1}
    search = deep_permuter.Search(net, 1, 20, Log(), fn)
    assert search.metrics()['starting_percent'] == search.metrics()['best_percent'] == 99.9
    assert search.improvements == 0
