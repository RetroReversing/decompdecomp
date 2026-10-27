"""Structured progress is a copied snapshot independent of the detailed log."""
import threading

from decompnet.app.support import Jobs


def test_job_progress_replaces_snapshot_without_mutating_previous_values():
    published, finish = threading.Event(), threading.Event()
    jobs = Jobs()
    def work(log):
        value = {'tested': 1}
        log.progress(value)
        value['tested'] = 99
        published.set()
        finish.wait(5)
    jid = jobs.start('test', work)
    try:
        assert published.wait(5)
        assert jobs.jobs[jid]['progress'] == {'tested': 1}
        assert jobs.jobs[jid]['log'] == []
    finally:
        finish.set()
