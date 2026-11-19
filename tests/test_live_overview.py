"""The live poll's overview rebuilds once per project, so one huge project cannot take both live lane slots.

A 38,694-function project rebuilt its overview (minutes) on every poll; two concurrent polls took both slots and
every other project's page queued behind them. A second poll for the same project now answers with the cheap
overview instead of starting another rebuild.
"""

import threading
from types import SimpleNamespace

from decompnet.app.engine.gui import GuiMixin


def fake_engine(*, builds):
    """A stand-in with just what live_overview uses; `builds` counts full overview runs."""
    started, release = threading.Event(), threading.Event()

    class Fake:
        LIVE_OVERVIEW_TTL = 10.0
        LIVE_VOLATILE = ('autopilot',)

        def _project(self, slug):
            return {'slug': slug, 'id': 1, 'config': '{}'}

        def project_store(self, pid):
            return SimpleNamespace(_local=SimpleNamespace(in_tx=False))

        def live_overview_key(self, proj):
            return (proj['slug'], 1, 'revision')

        def overview(self, slug, room_members=True, quick=False):
            if quick:
                return {'name': slug, 'overview_pending': True}
            builds.append(slug)
            started.set()
            release.wait(5)
            return {'name': slug, 'functions': 10}

        def live_volatile(self, slug, stable):
            return {'autopilot': None}

    return Fake(), started, release


def test_a_second_poll_does_not_start_another_rebuild():
    builds = []
    engine, started, release = fake_engine(builds=builds)
    first = {}

    thread = threading.Thread(target=lambda: first.update(result=GuiMixin.live_overview(engine, 'huge')))
    thread.start()
    assert started.wait(2), 'the first poll should have started its rebuild'

    overview, version = GuiMixin.live_overview(engine, 'huge')  # while the rebuild is still running
    assert overview.get('overview_pending') is True
    assert version == 'pending'
    assert builds == ['huge'], 'the second poll must not start a rebuild of its own'

    release.set()
    thread.join(5)
    assert first['result'][0]['functions'] == 10, 'the first poll returns the full overview'
    assert builds == ['huge']


def test_after_a_rebuild_the_cache_serves_polls_until_the_ttl():
    builds = []
    engine, started, release = fake_engine(builds=builds)
    release.set()  # don't block: the first call completes at once

    GuiMixin.live_overview(engine, 'huge')
    overview, version = GuiMixin.live_overview(engine, 'huge')

    assert overview['functions'] == 10 and 'overview_pending' not in overview
    assert version != 'pending'
    assert builds == ['huge'], 'a cached overview is not rebuilt'
