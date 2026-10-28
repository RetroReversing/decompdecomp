"""Durable project-wide deep passes; each candidate gets a bounded turn through the normal judges."""
import hashlib
import json
import threading
import time

from .. import candidate_scope
from ..network import NetworkError
from . import agent_tokens, deep_permuter

POLL_SECONDS = 15
SCHEMA = '''CREATE TABLE IF NOT EXISTS deep_permutation_passes(
 function_id INTEGER PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL,
 minutes INTEGER NOT NULL, max_variants INTEGER NOT NULL, completed_at REAL,
 result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '')'''


def signature(net, proj, fn, minutes, variants):
    return hashlib.sha256(repr(('deep-pass-v1', agent_tokens.target_scope(net, fn, ''),
                               fn['best_source'], minutes, variants)).encode()).hexdigest()


class Sweeps:
    def __init__(self, net, jobs):
        self.net, self.jobs = net, jobs
        self.active = {}
        self.lock = threading.RLock()
        with net.store.tx() as db:
            db.execute(SCHEMA)

    def config(self, slug):
        proj = self.net.project_row(slug)
        row = self.net.store.one('SELECT value FROM settings WHERE key=?', f'deep-sweep:{proj["id"]}')
        return json.loads(row['value']) if row else {'enabled': False, 'minutes': 5, 'max_variants': 10000}

    def save(self, slug, cfg):
        proj = self.net.project_row(slug)
        with self.net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                       (f'deep-sweep:{proj["id"]}', json.dumps(cfg)))

    def history(self, slug, fn):
        row = self.net.store.one('SELECT * FROM deep_permutation_passes WHERE function_id=?', fn['id'])
        if not row:
            return {'state': 'not tried', 'completed': False}
        result = dict(row)
        result['stale'] = row['scope'] != signature(self.net, self.net.project_row(slug), fn,
                                                   row['minutes'], row['max_variants'])
        result['completed'] = row['state'] == 'completed' and not result['stale']
        result['result'] = json.loads(row['result'])
        return result

    def status(self, slug):
        proj = self.net.project_row(slug)
        counts = self.net.store.one("SELECT COUNT(*) total, SUM(best_source IS NOT NULL AND best_score IS NOT NULL) ready "
                                    "FROM functions WHERE project_id=? AND status='open'", proj['id'])
        completed = self.net.store.one("SELECT COUNT(*) n FROM deep_permutation_passes d JOIN functions f ON f.id=d.function_id "
                                      "WHERE f.project_id=? AND d.state='completed'", proj['id'])['n']
        errors = self.net.store.one("SELECT COUNT(*) n FROM deep_permutation_passes d JOIN functions f ON f.id=d.function_id WHERE f.project_id=? AND d.state='error'", proj['id'])['n']
        with self.lock:
            current = dict(self.active.get(slug, {}))
        job = self.jobs.jobs.get(current.get('job'), {})
        return self.config(slug) | {'total': counts['total'], 'ready': counts['ready'] or 0,
                                   'completed': completed, 'errors': errors, 'state': job.get('state', 'idle'), **current,
                                   'progress': job.get('progress', {}), 'error': job.get('error')}

    def start(self, slug, minutes=5, max_variants=10000, repeat=False):
        if (isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 120 or
                isinstance(max_variants, bool) or not isinstance(max_variants, int) or not 1 <= max_variants <= 1000000):
            raise NetworkError('Choose 1–120 minutes and 1–1,000,000 variants per function.')
        self.net.project_row(slug)
        if slug in self.net.links:
            raise NetworkError('Run the deep pass on the room host’s project.')
        with self.lock:
            previous = self.active.get(slug, {})
            if self.jobs.jobs.get(previous.get('job'), {}).get('state') == 'running':
                raise NetworkError('A deep pass is already running for this project. Stop it before starting another.')
            if repeat:
                with self.net.store.tx() as db:
                    db.execute("DELETE FROM deep_permutation_passes WHERE function_id IN "
                               "(SELECT id FROM functions WHERE project_id=? AND status='open')",
                               self.net.project_row(slug)['id'])
            self.save(slug, {'enabled': True, 'minutes': minutes, 'max_variants': max_variants})
            job = self.jobs.start('deep-permutation-pass', lambda log: self.run(slug, minutes, max_variants, log),
                                  cancel_message='Deep pass stopped. Verified improvements and completed passes are saved.')
            self.active[slug] = {'job': job}
        return self.status(slug)

    def stop(self, slug):
        with self.lock:
            cfg = self.config(slug)
            self.save(slug, cfg | {'enabled': False})
            job = self.active.get(slug, {}).get('job')
            if job:
                self.jobs.cancel(job)
        return self.status(slug)

    def resume(self):
        # An interrupted function is retried; only completed bounded turns are skipped.
        for proj in self.net.store.all('SELECT slug FROM projects'):
            cfg = self.config(proj['slug'])
            if cfg['enabled']:
                try:
                    self.start(proj['slug'], cfg['minutes'], cfg['max_variants'])
                except NetworkError as error:
                    self.save(proj['slug'], cfg | {'enabled': False, 'resume_error': str(error)})
                    self.jobs.logger.warning('Deep pass could not resume: %s', error)

    def shutdown(self):
        # Preserve enabled intent for the next launch, but let current searches release claims.
        with self.lock:
            for current in self.active.values():
                self.jobs.cancel(current['job'])

    def completed(self, slug, before, minutes, variants, result, build_scope):
        fresh = self.net._function(before['name'], slug)
        proj = self.net.project_row(slug)
        stable = build_scope == candidate_scope.key(self.net, proj, before)
        self.record(fresh, signature(self.net, proj, fresh, minutes, variants),
                    'completed' if stable else 'pending', minutes, variants, result)

    def record(self, fn, scope, state, minutes, variants, result=None, error=''):
        with self.net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO deep_permutation_passes '
                       '(function_id,scope,state,minutes,max_variants,completed_at,result,error) VALUES(?,?,?,?,?,?,?,?)',
                       (fn['id'], scope, state, minutes, variants, time.time() if state == 'completed' else None,
                        json.dumps(result or {}), error))

    def run(self, slug, minutes, variants, log):
        """Finish one turn per unchanged candidate; wait for claims or usable C without marking them done."""
        visited = tested = matched = 0
        while True:
            log.check_cancel()
            proj = self.net.project_row(slug)
            pending = waiting = 0
            rows = self.net.store.all("SELECT * FROM functions WHERE project_id=? AND status='open' ORDER BY size,id", proj['id'])
            for row in rows:
                log.check_cancel()
                fn = self.net._function(row['name'], slug)
                if fn['status'] != 'open':
                    continue
                if not fn['best_source'] or fn['best_score'] is None:
                    waiting += 1
                    continue
                scope = signature(self.net, proj, fn, minutes, variants)
                previous = self.net.store.one('SELECT * FROM deep_permutation_passes WHERE function_id=?', fn['id'])
                if previous and previous['scope'] == scope and previous['state'] in ('completed', 'error'):
                    continue
                pending += 1
                if self.net.store.one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time()):
                    waiting += 1
                    continue
                build_scope = candidate_scope.key(self.net, proj, fn)
                self.record(fn, scope, 'running', minutes, variants)
                log(f'Deep pass: {fn["name"]} · up to {minutes} minutes / {variants:,} variants')
                total = len(rows)
                def progress(value, name=fn['name'], done=visited, wins=matched, total=total, blocked=waiting):
                    log.progress({'function': name, 'functions_tested': done, 'matches': wins,
                                  'total_unmatched': total, 'waiting': blocked} | value)
                def function_log(line):
                    log(line)
                function_log.check_cancel = log.check_cancel
                function_log.progress = progress
                try:
                    result = deep_permuter.run(self.net, slug, fn['name'], minutes, variants, function_log)
                    # Also records test backends and older search providers that do not publish history.
                    # The actual single-function search uses the same completion gate.
                    self.completed(slug, fn, minutes, variants, result, build_scope)
                    visited += 1
                    tested += result['tested']
                    matched += int(result['matched'])
                except NetworkError as error:
                    # Claim races are temporary. A bad unchanged candidate stays visible as an error,
                    # not completed, and a changed source/header or an explicit repeat retries it.
                    busy = self.net.store.one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time())
                    self.record(fn, scope, 'pending' if busy or 'claim' in str(error).lower() or str(error).startswith('This function is working') else 'error', minutes, variants, error=str(error))
                    log(f'{fn["name"]}: {error}')
                except BaseException:
                    self.record(fn, scope, 'pending', minutes, variants)
                    raise
                progress({'phase': 'moving to next function', 'tested': tested, 'functions_tested': visited, 'matches': matched})
            if not pending and not waiting:
                self.save(slug, self.config(slug) | {'enabled': False})
                log('Deep pass finished. Completed bounded passes are saved; this is not an exhaustive search of all C.')
                return {'functions_tested': visited, 'tested': tested, 'matched': matched}
            log.progress({'phase': 'waiting for unclaimed functions or scored C', 'functions_tested': visited,
                          'matches': matched, 'waiting': waiting, 'total_unmatched': len(rows)})
            # Cancellation remains responsive while another worker owns the remaining functions.
            for _ in range(POLL_SECONDS):
                log.check_cancel()
                time.sleep(1)
