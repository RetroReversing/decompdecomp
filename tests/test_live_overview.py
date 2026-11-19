"""A project's live overview is rebuilt off the request thread, once, and a failure backs off.

It used to be rebuilt inside the poll: on a 38,694-function project that took minutes, two concurrent polls took
both slots of the app's two-slot live lane, and every other project's page sat at "loading additional project
details" behind them.
"""

import threading
import time
from types import SimpleNamespace

from decompnet.app.engine.gui import GuiMixin


class Fake(GuiMixin):
    LIVE_OVERVIEW_TTL = 10.0
    LIVE_BUILD_BACKOFF = 30.0
    LIVE_VOLATILE = ('autopilot',)

    def __init__(self, *, fail=False, functions=4):
        self.functions = functions
        self.builds = []
        self.fail = fail
        self.started = threading.Event()
        self.release = threading.Event()

    def _project(self, slug):
        return {'slug': slug, 'id': 1, 'config': '{}'}

    def project_store(self, pid):
        return SimpleNamespace(_local=SimpleNamespace(in_tx=False))

    def live_overview_key(self, proj):
        return (proj['slug'], 1, 'revision')

    def overview(self, slug, room_members=True, quick=False):
        if quick:
            return {'name': slug, 'quick': True}
        self.builds.append(slug)
        self.started.set()
        self.release.wait(5)
        if self.fail:
            raise RuntimeError('the overview cannot be built')
        return {'name': slug, 'functions': 10}

    def live_volatile(self, slug, stable):
        return {'autopilot': None}

    def _live_function_count(self, proj):
        return self.functions

    def wait_for_build(self, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.__dict__.get('_live_building'):
                return True
            time.sleep(0.02)
        return False


def test_the_poll_answers_at_once_and_the_build_runs_in_the_background():
    engine = Fake()
    overview, version = GuiMixin.live_overview(engine, 'huge')

    assert overview['overview_pending'] is True  # the page paints now
    assert engine.started.wait(2), 'a build should have started'
    assert engine.builds == ['huge']
    assert '.' in version and len(version.split('.')[1]) == 12  # a well-formed digest.moving token

    engine.release.set()
    assert engine.wait_for_build()

    overview, _ = GuiMixin.live_overview(engine, 'huge')
    assert overview['functions'] == 10 and overview['overview_pending'] is False
    assert engine.builds == ['huge'], 'the cached overview is served, not rebuilt'


def test_one_build_at_a_time_per_project():
    engine = Fake()
    GuiMixin.live_overview(engine, 'huge')
    assert engine.started.wait(2)

    for _ in range(3):  # the page polls about once a second
        overview, _ = GuiMixin.live_overview(engine, 'huge')
        assert overview['overview_pending'] is True

    assert engine.builds == ['huge'], 'further polls must not start their own builds'
    engine.release.set()
    assert engine.wait_for_build()


def test_a_failing_build_backs_off_instead_of_spawning_a_thread_each_poll():
    engine = Fake(fail=True)
    engine.release.set()  # don't block, just fail
    GuiMixin.live_overview(engine, 'broken')
    assert engine.wait_for_build()

    for _ in range(3):
        overview, _ = GuiMixin.live_overview(engine, 'broken')
        assert overview['overview_pending'] is True

    assert engine.builds == ['broken'], 'the backoff stops a hot loop on a project that always fails'


def test_an_absurdly_large_project_is_not_summarised_automatically():
    """A summary is read off the request thread, but it still holds the GIL while it runs: past an absurd size the
    poll paints with the cheap overview instead, so one project cannot throttle the rest. (The limit is high
    because the reads are cheap when they are done right: 38k functions take under two seconds.)"""
    engine = Fake(functions=500_000)
    overview, _ = GuiMixin.live_overview(engine, 'huge')

    assert overview['overview_pending'] is True
    assert engine.builds == [], 'no full overview build should start'
    assert not engine.__dict__.get('_live_building')


class Busy(Fake):
    """A project whose agents move `attempts` and the newest event on every read of its key."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.counter = 0

    def live_overview_key(self, proj):
        self.counter += 1
        return (proj['slug'], 1, 'revision', self.counter)


def test_a_key_that_moves_during_the_build_still_caches_the_overview():
    """Discarding a build whose key moved left a busy project's page on "loading additional project details"
    indefinitely: its key moves during every build."""
    engine = Busy()
    engine.release.set()
    GuiMixin.live_overview(engine, 'busy')
    assert engine.wait_for_build()

    overview, _ = GuiMixin.live_overview(engine, 'busy')
    assert overview['overview_pending'] is False and overview['functions'] == 10
    engine.wait_for_build()


def test_the_cached_overview_is_served_while_it_is_rebuilt():
    """An expired entry is served while its rebuild runs: answering with the cheap overview instead flashed the
    page back to "loading additional project details" every time the cache expired."""
    engine = Fake()
    engine.release.set()
    GuiMixin.live_overview(engine, 'proj')
    assert engine.wait_for_build()

    engine.release.clear()
    engine.started.clear()
    key, _, stable, digest, settled = engine._live_overviews['proj']
    engine._live_overviews['proj'] = (key, time.monotonic() - 1, stable, digest, settled)  # expired

    overview, _ = GuiMixin.live_overview(engine, 'proj')
    assert overview['overview_pending'] is False and overview['functions'] == 10
    assert engine.started.wait(2), 'the expired entry is rebuilt behind the poll'
    overview, _ = GuiMixin.live_overview(engine, 'proj')
    assert overview['overview_pending'] is False, 'and still served while that rebuild runs'
    engine.release.set()
    assert engine.wait_for_build()
    assert engine.builds == ['proj', 'proj']


def test_a_costly_overview_waits_out_its_cost_before_a_moved_key_rebuilds_it():
    engine = Busy()
    engine.release.set()
    GuiMixin.live_overview(engine, 'busy')
    assert engine.wait_for_build()
    key, expires, stable, digest, _ = engine._live_overviews['busy']
    engine._live_overviews['busy'] = (key, expires, stable, digest, time.monotonic() + 60)  # as a slow build sets

    for _ in range(3):
        overview, _ = GuiMixin.live_overview(engine, 'busy')
        assert overview['overview_pending'] is False
    assert engine.builds == ['busy'], 'a moved key alone does not rebuild a costly overview back to back'
