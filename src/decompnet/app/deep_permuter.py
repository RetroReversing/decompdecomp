"""User-requested, bounded searches of one saved candidate, using the ordinary permuter judges.

The finite local candidate list can be visited completely. Random C rewriting has no finite exhaustive
search space, so time/variant limits and cancellation always remain explicit.
"""
import hashlib
import json
import time

from .. import scoring
from ..network import NetworkError
from ..project import compiler_for
from ..repair_feedback import current_trial
from ..repair_feedback import trial as repair_trial
from . import (
    adaptive_repairs,
    agent_tokens,
    draft_repairs,
    permuter_focus,
    permuter_local,
    repair_ranking,
    search_frontier,
    stack_padding_repairs,
)
from .permute import Permuter, State

AFTER_RANDOM_TRIALS = 32


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
    def __init__(self, net, minutes, max_variants, log, fn=None):
        super().__init__(net)
        self.deadline = time.monotonic()+minutes*60
        self.seconds = minutes*60
        self.limit = max_variants
        self.log = log
        self.tested = self.skipped = self.visited = self.rejected = 0
        self.starting_score = fn['best_score'] if fn is not None else None
        self.best = self.starting_score
        self.max_score = max(1, fn['n_insns'] or 0) * scoring.PENALTY_INSERTION if fn is not None else None
        self.improvements = 0
        self.baseline_measured = False
        self.phase = 'local repairs'
        self.measuring_baseline = False
        self.record_improvement = lambda score: None
        self.recorded_best = None
        self.local_seen = set()
        self.refinement_rounds = 0
        self.winner_diff = None
        self.winner_source = None
        self.trace_variants = True
        self.recipe_counts = {}
        self.measurement_counts = {}
        self.recipe_wins = []
        self.recipe_fn = fn['id'] if fn is not None else None
        self.current_family = ''
        self.last_outcome = ''
        self.last_score = None

    def check(self):
        self.log.check_cancel()
        if time.monotonic() >= self.deadline:
            raise SearchLimit('Time limit reached')
        if self.tested >= self.limit or self.visited >= self.limit*20:
            raise SearchLimit('Variant limit reached')

    def metrics(self):
        """Use the ordinary score scale; a near miss must never round up to a displayed 100%."""
        def percent(score):
            if score is None or self.max_score is None:
                return None
            return 100.0 if score == 0 else min(99.9, int(max(0, 100*(1-score/self.max_score))*10)/10)
        return {'starting_score': self.starting_score, 'starting_percent': percent(self.starting_score),
                'best_score': self.best, 'best_percent': percent(self.best), 'improvements': self.improvements}

    def report(self):
        if self.best is not None and (self.recorded_best is None or self.best < self.recorded_best):
            self.record_improvement(self.best)
            self.recorded_best = self.best
        self.log.progress({'phase': self.phase, 'tested': self.tested, 'skipped': self.skipped,
                           **self.metrics(), 'variant_limit': self.limit, 'rejected': self.rejected,
                           'seconds_left': max(0, round(self.deadline-time.monotonic())), 'refinement_rounds': self.refinement_rounds,
                           'current_family': self.current_family, 'last_outcome': self.last_outcome,
                           'last_score': self.last_score, 'visited': self.visited})

    def recipe(self, fn, source, best, score, outcome, elapsed_ms=0):
        provenance = current_trial() or {'kind': self.phase, 'family': 'unattributed'}
        family = provenance.get('family') or 'unattributed'
        detail = ('compile failed' if getattr(self, '_trial_outcome', '') == 'compile_failed' else 'unscored')
        self.current_family, self.last_outcome, self.last_score = (
            family, detail if outcome == 'tested' and score is None else outcome, score)
        self.recipe_fn = fn['id']
        counts = self.recipe_counts.setdefault(family, [0, 0, 0, 0, 0, 0])
        counts[{'tested': 0, 'skipped': 1, 'rejected': 2}[outcome]] += 1
        if outcome == 'tested':
            values = self.measurement_counts.setdefault((family, self.phase), [0, 0, 0, 0, 0, 0, 0, 0, 0])
            values[0] += 1
            if score is None:
                values[1 if getattr(self, '_trial_outcome', '') == 'compile_failed' else 2] += 1
            elif score == best:
                values[3] += 1
            elif score > best:
                values[4] += 1
            else:
                values[5] += 1
                values[6] += int(score == 0)
                values[8] += best - score
            values[7] += elapsed_ms
        if outcome == 'tested' and score is not None and score < best:
            self.last_outcome = 'improved'
            counts[3] += 1
            counts[4] += int(score == 0)
            counts[5] += best - score
            info = dict(provenance) | {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                                      'phase': self.phase}
            self.recipe_wins.append((family, best, score, json.dumps(info), time.time()))
            self.flush_recipes()
            self.log(f'Improved with {family}: score {best:,} → {score:,}; verified and saved.')
        elif sum(sum(v[:3]) for v in self.recipe_counts.values()) >= 64:
            self.flush_recipes()

    def flush_recipes(self):
        sweeps = getattr(self.net, 'deep_sweeps', None)
        if sweeps and self.recipe_fn is not None and (self.recipe_counts or self.recipe_wins or self.measurement_counts):
            sweeps.record_recipes(self.recipe_fn, self.recipe_counts, self.recipe_wins, self.measurement_counts)
        self.recipe_counts.clear()
        self.recipe_wins.clear()
        self.measurement_counts.clear()

    def _compiler_preflight(self, proj, fn, state, deadline):
        from .matching_preflight import run
        self.phase = 'compiler preflight'
        self.report()
        result = run(self.net, proj, fn, self.log, self.check, max_profiles=self.limit-self.tested)
        self.tested += result['tested']
        if result.get('link'):
            raise NetworkError(result['link']['message'])
        if self.net._function(fn['name'], proj['slug'])['status'] == 'matched' and self.best != 0:
            self.best = 0
            self.improvements += int(bool(result.get('applied')))
        return result

    def _baseline(self, agent, proj, fn, state, fork):
        # Baseline evidence must come from today's compiler/header, including score-zero open rows.
        self.measuring_baseline = True
        try:
            measured = super()._baseline(agent, proj, fn, state, fork)
            if measured[0] is not None or fork:
                return measured
            from .prototype_alignment import variants
            header = self.net._context(proj['id'], fn['unit'])['header']
            for candidate in variants(fn['best_source'], fn['name'], header):
                score = self._score(agent, proj, fn, candidate, float('inf'), fork)
                fresh = self.net._function(fn['name'], proj['slug'])
                if score is not None and fresh['status'] == 'open' and fresh['best_source'] == fn['best_source']:
                    # The old percentage described C which no longer compiles. Retain that source in
                    # history and rebase only this broken draft after a normal compiler/judge result.
                    self.net._record(fn, agent, None, fn['best_source'],
                                     self.net._context(proj['id'], fn['unit'])['version'],
                                     compiled=False, via='permuter')
                    with self.net.store.tx() as db:
                        current = self.net._function(fn['name'], proj['slug'])
                        unchanged = (current['status'] == 'open' and current['best_source'] == fn['best_source']
                                     and all(current[k] == fn[k] for k in ('best_score', 'target', 'relocs', 'vram'))
                                     and self.net._project(proj['slug'])['config'] == proj['config']
                                     and self.net._context(proj['id'], fn['unit'])['header'] == header)
                        if unchanged:
                            db.execute("UPDATE functions SET best_source=?,best_score=?,best_agent_id=? WHERE id=? AND status='open'",
                                       (candidate, score, agent.id, fn['id']))
                    fresh = self.net._function(fn['name'], proj['slug'])
                if score is not None and (fresh['best_source'] == candidate or fresh['status'] == 'matched'):
                    self.log('Repaired a conflicting return type in the candidate; shared header unchanged. '
                             'The broken draft is retained in history; the new baseline may have a lower percentage.')
                    return score, measured[1]+1
            return measured
        finally:
            self.measuring_baseline = False

    def local_repairs(self, agent, proj, fn, best, fork, max_trials=None):
        """Regenerate from each saved winner, sharing this search's deadline, cache and allowance."""
        flags = compiler_for(self.net.cfg(proj), fn['vram']).get('flags', []) if not fork else []
        trials = 0
        detours_used = set()
        alternatives = search_frontier.Frontier()
        source = fn['best_source']
        context = self.net._context(proj['id'],fn['unit'])
        header = self.net.forks.permuter_header(fork,fn['name']) if fork else (context['header'] if context else '')
        while best > 0:
            self.check()
            self.local_seen.add(source)
            rows = repair_ranking.evidence(self.net, fn['id'], agent.id)
            labels = {}
            choices = draft_repairs.variants(source, fn['name'], proj['arch'], fn['asm'] or '', flags, labels=labels, limit=None, header=header)
            stack = stack_padding_repairs.grow(source, fn['name'], proj['arch'], rows)
            for candidate in stack:
                labels.setdefault(candidate, 'stack_padding')
            choices += stack
            for family, candidate in permuter_local.variants(source, fn['name']):
                labels.setdefault(candidate, family)
                choices.append(candidate)
            families = list(dict.fromkeys(labels.get(c, 'local_repair') for c in choices))
            ranked = adaptive_repairs.order(self.net, proj, fn, rows, repair_ranking.order(families, rows))
            # Every other burst keeps the original order for exploration.
            if self.refinement_rounds % 2 == 0:
                choices.sort(key=lambda c: ranked.index(labels.get(c, 'local_repair')))

            for candidate in dict.fromkeys(choices):
                if candidate in self.local_seen:
                    continue
                if max_trials is not None and trials >= max_trials:
                    return best, self.net._function(fn['name'], proj['slug'])['best_source']
                self.local_seen.add(candidate)
                trials += 1
                with repair_trial({'kind': 'deep local repair', 'family': labels.get(candidate, 'local_repair'),
                                   'base_source_sha256': hashlib.sha256(source.encode()).hexdigest()}):
                    score = self._score(agent, proj, fn, candidate, best, fork)
                if score is not None and best < score <= best * 1.25 and not getattr(self, '_trial_reused', False):
                    measured_rows = repair_ranking.evidence(self.net, fn['id'], agent.id)
                    alternatives.offer(candidate, score, measured_rows)
                if score is not None and score < best:
                    best = score
                    fn = self.net._function(fn['name'], proj['slug'])
                    source = fn['best_source']
                    break
            else:
                detour = next((node for node in alternatives.nodes
                               if node['source'] not in detours_used and node['source'] not in self.local_seen), None)
                # Local trials are already in local_seen; allow each retained source one expansion.
                if detour is None:
                    detour = next((node for node in alternatives.nodes if node['source'] not in detours_used), None)
                if detour and len(detours_used) < 3:
                    detours_used.add(detour['source'])
                    source = detour['source']
                    self.log('Trying a retained instruction-shape alternative; saved best C is unchanged.')
                    continue
                break
        return best, self.net._function(fn['name'], proj['slug'])['best_source']

    def _after_improvement(self, agent, proj, fn, state, fork, best, source):
        previous = self.phase
        self.phase = 'local repairs after random improvement'
        self.refinement_rounds += 1
        try:
            self.log('Random rewrite improved the candidate; trying targeted local repairs on its new diff.')
            # Keep the exploratory lane alive: at most 32 local proposals after each random win.
            # The original global time/compile limit always wins over this burst limit.
            fresh = self.net._function(fn['name'], proj['slug'])
            return self.local_repairs(agent, proj, fresh, best, fork, max_trials=AFTER_RANDOM_TRIALS)
        finally:
            if self.winner_diff is not None:
                # A losing local trial must not steer the next random batch's weights. Restore
                # evidence for the saved winner, without spending another compiler invocation.
                if hasattr(self.net, 'keep_diff'):
                    self.net.keep_diff(agent.id, fn['id'], self.winner_diff[0])
                else:
                    self.net._last_diffs[agent.id, fn['id']] = self.winner_diff
                if hasattr(self.net, '_agent_outcomes'):
                    self.net._agent_outcomes[agent.id, fn['id']] = {
                        'source': self.winner_source, 'score': self.best, 'rows': self.winner_diff[0]}
            self.phase = previous

    def _score(self, agent, proj, fn, source, best, fork):
        self.check()
        self.visited += 1
        if not fork and not self.measuring_baseline and source != fn['best_source']:
            from .repair_preflight import invalid_call
            header = self.net._context(proj['id'], fn['unit'])['header']
            if invalid_call(source, fn['name'], header):
                self.rejected += 1
                self.recipe(fn, source, best, None, 'rejected')
                self.report()
                return None
        # Scope includes the actual compiler/header/target and imported build dependencies. A previous
        # miss is reusable only in that scope. Better cached results are rejudged before being kept.
        scope = agent_tokens.target_scope(self.net, {**dict(fn), 'best_source': source}, '')
        key = 'deep-v1:'+hashlib.sha256((scope+source).encode()).hexdigest()
        cached = self.net.store.one('SELECT feedback FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key)
        score = json.loads(cached['feedback']) if cached and cached['feedback'] else None
        reused = not self.measuring_baseline and isinstance(score, int) and score >= best
        self._trial_reused = reused
        elapsed_ms = 0
        if reused:
            self.skipped += 1
        else:
            self.tested += 1
            self.current_family = (current_trial() or {}).get('family', 'baseline' if self.measuring_baseline else 'unattributed')
            self.last_outcome, self.last_score = 'compiling', None
            self.report()
            self._trial_outcome = 'unscored'
            rows = repair_ranking.evidence(self.net, fn['id'], agent.id)
            policy_focus = permuter_focus.classify(rows)
            policy_profile = adaptive_repairs.profile(self.net, proj, fn)
            started = time.monotonic()
            score = super()._score(agent, proj, fn, source, best, fork)
            elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
            if not self.measuring_baseline:
                adaptive_repairs.record(self.net, policy_profile, policy_focus, self.current_family, best, score, elapsed_ms, origin="deep permuter")
            if score is not None and score > 0:
                with self.net.store.tx() as db:
                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(?,?,?,?,?)',
                               (fn['id'], key, 'miss', 'deep function search', json.dumps(score)))
        if not self.measuring_baseline:
            self.recipe(fn, source, best, score, 'skipped' if reused else 'tested', elapsed_ms)
            if self.tested and self.tested % 100 == 0 and not reused:
                family = (current_trial() or {}).get('family', 'unattributed')
                self.log(f'{self.phase}: {self.tested:,} compiled, {self.skipped:,} cached, '
                         f'{self.rejected:,} rejected; latest {family}, best score {self.best}; '
                         f'{max(0, round(self.deadline-time.monotonic()))} seconds left.')
        if score is not None:
            winner = False
            if self.measuring_baseline and not self.baseline_measured:
                self.starting_score = self.best = score
                self.baseline_measured = True
                winner = True
            elif not reused and not self.measuring_baseline and (self.best is None or score < self.best):
                if self.best is not None:
                    self.improvements += 1
                self.best = score
                winner = True
            if winner:
                self.winner_diff = getattr(self.net, '_last_diffs', {}).get((agent.id, fn['id']))
                self.winner_source = source
        self.report()
        return score


def run(net, slug, name, minutes, max_variants, log):
    """Visit the supported local repairs, then spend the remaining budget on random permutations."""
    proj, fn = validate(net, slug, name, minutes, max_variants)
    sweeps = getattr(net, 'deep_sweeps', None)
    if sweeps:
        from ..candidate_scope import key
        build_scope = key(net, proj, fn)
    search = Search(net, minutes, max_variants, log, fn)
    if sweeps:
        search.record_improvement = lambda score: sweeps.improvement(fn, score)
    search.report()
    state = State()
    agent, _ = net.local_agent('permuter')
    reason = 'Current candidate families finished'
    from .compiler_preflight import warnings
    warning = warnings(net, proj, fn)
    if warning:
        log('Compiler preflight (advisory): '+warning)
    log(f'Searching {name}: {minutes} minutes, up to {max_variants:,} compiled candidates. Checking the saved baseline first.')
    log('Local repairs follow the current instruction differences; then random rewrites explore further variants.')
    try:
        search._compiler_preflight(proj, fn, state, search.deadline)
        proj, fn = net.project_row(slug), net._function(name, slug)
        if fn['status'] == 'matched':
            reason = 'Matched'
        if reason != 'Matched':
            if not search._hold(agent, fn, count_turn=False):
                raise NetworkError('This function was claimed meanwhile; try again after it is released.')
            try:
                fork = net.fork_slug(slug)
                # Fresh baseline evidence also diagnoses obsolete candidate-owned prototypes.
                search.phase = 'checking baseline'
                baseline, _ = search._baseline(agent, proj, fn, state, fork)
                if baseline is None:
                    raise NetworkError('The saved C does not compile in the current context; fix its declarations before deep search.')
                if baseline == 0:
                    reason = 'Matched'
                else:
                    search.phase = 'local repairs'
                    search.log('Baseline measured; trying targeted local repairs before random rewriting.')
                    best, _ = search.local_repairs(agent, proj, net._function(name, slug), baseline, fork)
                    if best == 0:
                        reason = 'Matched'
            finally:
                search._let_go(agent, fn)
        if reason != 'Matched':
            search.check()
            search.phase = 'random rewrites'
            search.log('Local repairs finished; generating random rewrites and judging each compiled candidate.')
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
    finally:
        search.flush_recipes()
    search.report()
    log(f'{reason}: {search.tested} candidates tested, {search.skipped} cached misses skipped, {search.rejected} invalid calls skipped. Verified improvements are saved immediately.')
    result = {'function': name, 'reason': reason, 'tested': search.tested, 'skipped': search.skipped,
              'rejected': search.rejected, 'refinement_rounds': search.refinement_rounds,
              'matched': net._function(name, slug)['status'] == 'matched', **search.metrics()}
    if sweeps:
        sweeps.completed(slug, fn, minutes, max_variants, result, build_scope)
    return result
