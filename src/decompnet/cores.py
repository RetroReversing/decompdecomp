"""How many processors this process may actually use.

A machine can report more cores than a process is allowed to run on: a container, a cpuset, or another program
pinning it with taskset. Sizing work by the reported count opened nineteen permuter lanes on a box with eight
usable cores, and the load average reached 25 while the app answered requests in seconds instead of milliseconds.
"""

from __future__ import annotations

import os


def usable_cores() -> int:
    """The processors this process may run on (its affinity mask), or the reported count where that isn't known."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)
