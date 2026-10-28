"""User-requested, bounded searches of one saved candidate, using the ordinary permuter judges.

The finite local candidate list can be visited completely. Random C rewriting has no finite exhaustive
search space, so time/variant limits and cancellation always remain explicit.
"""
import hashlib
import json
import time

from ..network import NetworkError
from ..project import compiler_for
from . import agent_tokens, draft_repairs, permuter_local, stack_padding_repairs
from .permute import Permuter, State


class SearchLimit(Exception):
    pass


def validate(net, slug, name, minutes, max_variants):
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 120:
        raise NetworkError('Choose a time limit from 1 to 120 minutes.')
    if isinstance(max_variants, bool) or not isinstance(max_variants, int) or not 1 <= max_variants <= 1000000:
        raise NetworkError('Choose a variant limit from 1 to 1,000,000.')
    proj = net.project_row(slug)
    fn = net._function(name, slug)
    if slug in net.links:
        raise NetworkError('Run deep permuter on the room host’s project.')
    if fn['status'] != 'open' or not fn['best_source'] or fn['best_score'] is None:
        raise NetworkError('Choose an unmatched function with saved, scored C first.')
    if net.store.one('SELECT 1 FROM claims WHERE function_id=? AND expires_at>?', fn['id'], time.time()):
        raise NetworkError('This function is working; stop its agent or wait for its claim to finish.')
    return proj, fn


class Search(Permuter):
    def __init__(self, net, minutes, max_variants, log):
        super().__init__(net)
        self.deadline = time.monotonic()+minutes*60
        self.seconds = minutes*60
        self.limit = max_variants
        self.log = log
        self.tested = self.skipped = self.visited = self.rejected = 0
        self.best = None
        self.phase = 'local repairs'
        self.measuring_baseline = False
        self.record_improvement = lambda score: None
        self.recorded_best = None

    def check(self):
        self.log.check_cancel()
        if time.monotonic() >= self.deadline:
            raise SearchLimit('Time limit reached')
        if self.tested >= self.limit or self.visited >= self.limit*20:
            raise SearchLimit('Variant limit reached')

    def report(self):
        if self.best is not None and (self.recorded_best is None or self.best < self.recorded_best):
            self.record_improvement(self.best)
            self.recorded_best = self.best
        self.log.progress({'phase': self.phase, 'tested': self.tested, 'skipped': self.skipped,
                           'best_score': self.best, 'variant_limit': self.limit, 'rejected': self.rejected,
                           'seconds_left': max(0, round(self.deadline-time.monotonic()))})

    def _baseline(self, *args):
        # A cached scalar score cannot supply the instruction rows needed by targeted repairs.
        # The parent still caches a measured baseline within this search instance.
        self.measuring_baseline = True
        try:
            return super()._baseline(*args)
        finally:
            self.measuring_baseline = False

    def _score(self, agent, proj, fn, source, best, fork):
        self.check()
        self.visited += 1
        if not fork and not self.measuring_baseline and source != fn['best_source']:
            from .repair_preflight import invalid_call
            header = self.net._context(proj['id'], fn['unit'])['header']
            if invalid_call(source, fn['name'], header):
                self.rejected += 1
                self.report()
                return None
        # Scope includes the actual compiler/header/target and imported build dependencies. A previous
        # miss is reusable only in that scope. Better cached results are rejudged before being kept.
        scope = agent_tokens.target_scope(self.net, {**dict(fn), 'best_source': source}, '')
        key = 'deep-v1:'+hashlib.sha256((scope+source).encode()).hexdigest()
        cached = self.net.store.one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key)
        score = json.loads(cached['feedback']) if cached and cached['feedback'] else None
        if not self.measuring_baseline and isinstance(score, int) and score >= best:
            self.skipped += 1
        else:
            self.tested += 1
            score = super()._score(agent, proj, fn, source, best, fork)
            if score is not None and score > 0:
                with self.net.store.tx() as db:
                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                               (fn['id'], key, 'miss', 'deep function search', json.dumps(score)))
        if score is not None:
            self.best = score if self.best is None else min(self.best, score)
        self.report()
        return score


def run(net, slug, name, minutes, max_variants, log):
    """Visit the supported local repairs, then spend the remaining budget on random permutations."""
    proj, fn = validate(net, slug, name, minutes, max_variants)
    sweeps = getattr(net, 'deep_sweeps', None)
    if sweeps:
        from ..candidate_scope import key
        build_scope = key(net, proj, fn)
    search = Search(net, minutes, max_variants, log)
    if sweeps:
        search.record_improvement = lambda score: sweeps.improvement(fn, score)
    state = State()
    agent, _ = net.local_agent('permuter')
    reason = 'Current candidate families finished'
    from .compiler_preflight import warnings
    warning = warnings(net, proj, fn)
    if warning:
        log('Compiler preflight (advisory): '+warning)
    log('Testing supported local repairs, then bounded random rewrites. This cannot exhaust every possible C program.')
    try:
        if not search._hold(agent, fn, count_turn=False):
            raise NetworkError('This function was claimed meanwhile; try again after it is released.')
        try:
            fork = net.fork_slug(slug)
            flags = compiler_for(net.cfg(proj), fn['vram']).get('flags', []) if not fork else []
            # Fresh baseline evidence enables measured stack repairs and diagnoses stale saved scores.
            baseline, _ = search._baseline(agent, proj, fn, state, fork)
            if baseline is None:
                raise NetworkError('The saved C does not compile in the current context; fix it before deep search.')
            if baseline == 0:
                reason = 'Matched'
            else:
                best = baseline
                source = fn['best_source']
                rows = getattr(net, '_last_diffs', {}).get((agent.id, fn['id']), ([], ''))[0]
                choices = draft_repairs.variants(source, fn['name'], proj['arch'], fn['asm'] or '', flags, limit=None)
                choices += stack_padding_repairs.grow(source, fn['name'], proj['arch'], rows)
                choices += [s for _,s in permuter_local.variants(source, fn['name'])]
                for candidate in dict.fromkeys(choices):
                    score = search._score(agent, proj, fn, candidate, best, fork)
                    if score is not None and score < best:
                        best = score
                    if score == 0:
                        reason = 'Matched'
                        break
        finally:
            search._let_go(agent, fn)
        if reason != 'Matched':
            search.check()
            search.phase = 'random rewrites'
            search.seconds = max(1, search.deadline-time.monotonic())
            current = net._function(name, slug)
            if current['status'] == 'matched':
                reason = 'Matched'
            else:
                # _permute ignores the normal size/turn queue filters; the user selected this function.
                search._permute(agent, proj, current, state)
                if state.tried == 0:
                    raise NetworkError('This function was claimed meanwhile; try again after it is released.')
                if net._function(name, slug)['status'] == 'matched':
                    reason = 'Matched'
                elif time.monotonic() >= search.deadline:
                    reason = 'Time limit reached'
    except SearchLimit as error:
        reason = str(error)
    search.report()
    log(f'{reason}: {search.tested} candidates tested, {search.skipped} cached misses skipped, {search.rejected} invalid calls skipped. Verified improvements are saved immediately.')
    result = {'function': name, 'reason': reason, 'tested': search.tested, 'skipped': search.skipped,
              'rejected': search.rejected, 'matched': net._function(name, slug)['status'] == 'matched', 'best_score': search.best}
    if sweeps:
        sweeps.completed(slug, fn, minutes, max_variants, result, build_scope)
    return result
