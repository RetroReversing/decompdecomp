"""Bounded post-match analysis of frozen captures; never adopt source or change review state."""
import json
import logging
import os
import threading
import time

from . import repair_replay, workers

_SLOT = threading.BoundedSemaphore(1)
KEY = 'automatic_repair_analysis'
SCHEMA = '''CREATE TABLE IF NOT EXISTS repair_outcome_analysis(
 example_id INTEGER PRIMARY KEY REFERENCES repair_examples(id) ON DELETE CASCADE,
 state TEXT NOT NULL, report TEXT NOT NULL, updated_at REAL NOT NULL)'''


def prepare(feedback):
    with feedback.store.tx() as db:
        db.execute(SCHEMA)
        pending=tuple(feedback.net.__dict__.get('_repair_analysis_pending',()))
        placeholders=','.join('?' for _ in pending) or '-1'
        db.execute('UPDATE repair_outcome_analysis SET state=?,report=?,updated_at=? WHERE state=? AND example_id NOT IN ('+placeholders+')',
                   ('interrupted',json.dumps({'skipped':'Analysis interrupted. Run manual replay to retry; selected source is unchanged.'}),time.time(),'running',*pending))


def enabled(feedback):
    row = feedback.store.one('SELECT value FROM settings WHERE key=?',KEY)
    return row is None or row['value']=='true'


def configure(feedback,value):
    if not isinstance(value,bool):raise ValueError('Automatic repair analysis must be true or false.')
    with feedback.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(KEY,json.dumps(value)))


def result(feedback,case_id,include_source=True):
    prepare(feedback)
    row = feedback.store.one('SELECT * FROM repair_outcome_analysis WHERE example_id=?',case_id)
    if not row:return None
    report=json.loads(row['report'])
    if not include_source:
        # Compiler errors/dumps can quote source too. Summary exports retain only counts.
        minimized=report.get('minimization') or {}
        report={'summary':report.get('summary',{}),'minimization':{k:minimized[k] for k in
                ('edits','retained','reverted','exhausted') if k in minimized},
                'baseline_score':report.get('baseline_score'),'skipped':bool(report.get('skipped'))}
    return {'state':row['state'],'report':report}


def analyze(example):
    # Replay validates captured source/header hashes, installed toolchain and identical settings.
    # Four ablations plus two control compiles, no candidate-family search in the automatic pass.
    return repair_replay.replay(example,trials=4,seconds=20,minimize=True,diagnostics=True,ablation_only=True)


def schedule(feedback,case_id,expected_hash=None):
    if os.environ.get('DECOMP_REPAIR_ANALYSIS')=='off' or not hasattr(feedback.net,'forks'):return
    if not enabled(feedback) or not _SLOT.acquire(blocking=False):return
    lock = feedback.net.__dict__.setdefault('_repair_analysis_lock',threading.Lock())
    # Do not build an unbounded compiler queue when autopilot finds a large batch.
    with lock:
        pending = feedback.net.__dict__.setdefault('_repair_analysis_pending',set())
        if case_id in pending:
            _SLOT.release();return
        pending.add(case_id)
    def run():
        try:
            # The capture caller can still be inside its match transaction. Wait for its
            # commit before reading the example; rolled-back captures are never analyzed.
            with feedback.store.tx():pass
            if not enabled(feedback):return
            example = feedback.detail('',case_id)
            if expected_hash and example['evidence']['after']['source_sha256']!=expected_hash:return
            prepare(feedback)
            with feedback.store.tx() as db:
                db.execute('INSERT OR REPLACE INTO repair_outcome_analysis VALUES(?,?,?,?)',
                           (case_id,'running','{}',time.time()))
            report = analyze(example)
            with feedback.store.tx() as db:
                db.execute('UPDATE repair_outcome_analysis SET state=?,report=?,updated_at=? WHERE example_id=?',
                           ('done',json.dumps(report),time.time(),case_id))
        except Exception as error:
            logging.getLogger('decompdecomp').warning('Repair outcome analysis #%s: %s',case_id,error)
            try:
                prepare(feedback)
                with feedback.store.tx() as db:
                    db.execute('INSERT OR REPLACE INTO repair_outcome_analysis VALUES(?,?,?,?)',
                               (case_id,'failed',json.dumps({'skipped':str(error)[:300]}),time.time()))
            except Exception:pass  # capture may have rolled back or the project was removed
        finally:
            with lock:pending.discard(case_id)
            _SLOT.release()
    try:workers.pool().submit(run)
    except Exception as error:
        with lock:pending.discard(case_id)
        _SLOT.release()
        logging.getLogger('decompdecomp').warning('Unable to schedule repair analysis: %s',error)
