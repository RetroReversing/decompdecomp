"""How many processors may actually run: a machine can report more than a process is allowed to use."""

from decompnet import cores


def test_usable_cores_follows_the_affinity_mask(monkeypatch):
    """This box reports 12 processors and pins the app to 8. Sizing by the reported count opened nineteen lanes."""
    monkeypatch.setattr(cores.os, "sched_getaffinity", lambda pid: {0, 1, 4, 5, 6, 7, 10, 11}, raising=False)
    assert cores.usable_cores() == 8


def test_without_an_affinity_mask_the_reported_count_stands(monkeypatch):
    """Windows and macOS have no sched_getaffinity: the reported count is the best that can be known there."""
    monkeypatch.delattr(cores.os, "sched_getaffinity", raising=False)
    monkeypatch.setattr(cores.os, "cpu_count", lambda: 6)
    assert cores.usable_cores() == 6


def test_a_mask_with_nothing_in_it_still_leaves_one_core(monkeypatch):
    monkeypatch.setattr(cores.os, "sched_getaffinity", lambda pid: set(), raising=False)
    assert cores.usable_cores() == 1
