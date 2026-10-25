"""Coalesce new matches into bounded, low-priority cross-project searches, with no model calls.

One shared worker consumes one budget for the entire cascade. Engine-only uses stay inert until the server
explicitly enables this service; experiments and tests must opt in. A stopped project stays paused until Run again.
"""

import logging
import threading
import time

from . import free_candidates, similar_matches, workers
from .twins import Job

log = logging.getLogger('decompdecomp')


class Cascade:
    def __init__(self, net):
        self.net = net
        self.enabled = False
        self.seconds, self.attempts = 120, 128
        self.pending = set()
        self.paused = set()
        self.lock = threading.Lock()
        self.running = False
        self.active = None

    def configure(self, enabled, seconds=120, attempts=128):
        with self.lock:
            self.enabled = bool(enabled)
            self.seconds = max(1, min(600, int(seconds)))
            self.attempts = max(1, min(1024, int(attempts)))
            if not self.enabled:
                self.pending.clear()
                if self.active:
                    self.active.stop.set()

    def pause(self, slug):
        with self.lock:
            self.paused.add(slug)
            self.pending.discard(slug)
            if self.active and self.net.twins.jobs.get(slug) is self.active:
                self.active.stop.set()

    def resume(self, slug):
        with self.lock:
            self.paused.discard(slug)

    def notify(self, donor, fid):
        if not self.enabled or not self.net.is_rom(donor) or donor['arch'] != 'mips':
            return
        fn = self.net.store.one('SELECT size,status FROM functions WHERE id=?', fid)
        if not fn or fn['status'] != 'matched':
            return
        # Only compatible projects with open functions in this donor's size neighbourhood are affected.
        projects = self.net.store.all("SELECT DISTINCT p.* FROM projects p JOIN functions f ON f.project_id=p.id "
                                      "WHERE p.id!=? AND p.arch=? AND p.big_endian=? AND f.status='open' AND f.size BETWEEN ? AND ?",
                                      donor['id'], donor['arch'], donor['big_endian'], int(fn['size'] * .9), int(fn['size'] / .9))
        with self.lock:
            if not self.enabled:
                return
            self.pending.update(p['slug'] for p in projects if self.net.is_rom(p) and p['slug'] not in self.net.links
                                and p['slug'] not in self.paused)
            if self.running or not self.pending:
                return
            self.running = True
        workers.pool().submit(self.drain)

    def drain(self, job=None):
        """One global budget, including any matches unlocked by this wave. No recursive worker launches."""
        job = job or Job()
        failed = False
        free_candidates.budget(job, self.seconds, self.attempts)
        with self.lock:
            self.active = job
        try:
            while not free_candidates.exhausted(job):
                with self.lock:
                    if not self.enabled or not self.pending:
                        break
                    slug = sorted(self.pending)[0]
                    self.pending.remove(slug)
                    if slug in self.paused:
                        continue
                # An active autopilot already owns this project's pass. Its later matches can trigger another wave.
                with self.net.twins._lock:
                    if self.net.twins._full_running(slug):
                        continue
                    self.net.twins.jobs[slug] = job
                started = time.monotonic()
                count, size, tries = job.reuse_matched, job.reuse_bytes, job.reuse_attempts
                try:
                    job.matched += self.net.twins.across_games(slug, job)
                    job.matched += free_candidates.run(self.net.twins, slug, job)
                    job.matched += similar_matches.run(self.net.twins, slug, job)
                    proj = self.net.project_row(slug)
                    with self.net.store.tx() as db:
                        db.execute('INSERT INTO reuse_runs(project_id,attempts,matched,bytes,seconds,created_at) VALUES(?,?,?,?,?,?)',
                                   (proj['id'], job.reuse_attempts - tries, job.reuse_matched - count,
                                    job.reuse_bytes - size, time.monotonic() - started, time.time()))
                finally:
                    with self.net.twins._lock:
                        # Keep a completed snapshot for the GUI, without making later projects look active here.
                        snapshot = Job(state='stopped' if job.stop.is_set() else 'done', matched=job.reuse_matched - count,
                                       cross_tried=job.reuse_attempts - tries)
                        snapshot.ordered.set()
                        self.net.twins.jobs[slug] = snapshot
        except Exception:  # an optional heuristic must not interrupt agents or the server
            failed = True
            log.exception('automatic cross-project reuse failed')
        finally:
            job.state = 'stopped' if job.stop.is_set() else 'done'
            with self.lock:
                again = bool(not failed and self.enabled and self.pending and not free_candidates.exhausted(job))
                if not again:
                    self.pending.clear()  # cursors remain for Run again or the next new match
                self.active = None
                self.running = again
            if again:
                # A notification can arrive between the empty-queue check and cleanup. Keep the same budget.
                job.state = 'running'
                workers.pool().submit(lambda: self.drain(job))
