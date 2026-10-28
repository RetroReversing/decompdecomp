"""The permuter, in the background: the decomp community's way through the last few percent.

A function at 95% usually differs in register allocation or instruction order, which comes down to how the C
is written, not what it does: the order of statements and declarations, a temporary, a cast, `x += 1` for
`x++`, an if turned around. Agents doing it by hand spend turns (and tokens) on it, and stall. Here it runs on its
own, on spare CPU, on the near misses agents left (or autopilot's drafts reached): a worker holds a function for a
turn of a minute, so an agent that wants it gets it next, records every improvement as the function's best attempt
(agents start from it), and a match as a match. What matches is still judged byte for byte, like everything else.

Variants come from decomp-permuter (MIT, a pinned tool: Compilers & tools, or `decomp setup`): permuter_gen.py, run
in the tool's own environment, rewrites the function's C on its parse tree, BATCH variants at a time, with the
weights decomp-permuter tuned for the function's compiler. Our own judge scores each one (`_score`): a variant that
doesn't compile is skipped. Its rewrites don't all keep the meaning (it moves statements, retypes, deletes code), so
only what the judge says settles anything: a match is the same machine code as the game's, and an improvement is
only closer code for an agent to start from. Without the tool, or for C it can't read (a macro call of the source's
own), the six simple rewrites of permuter.py make the variants instead.

It runs on the background workers every project shares with the machine drafts (workers.py: one per spare core, up
to four), each at a lower CPU priority that the compilers it runs inherit: the app and the agents' compiles come first.
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import random
import subprocess
import threading
import time
from concurrent.futures import wait
from dataclasses import dataclass, field
from pathlib import Path

from .. import consoles, m2cfix, scoring, seeds, toolchains
from ..hostenv import NO_WINDOW
from ..network import NetworkError
from ..permuter import mutate
from ..project import compiler_for
from ..repair_feedback import trial as repair_trial
from . import draft_repairs, workers

log = logging.getLogger("decompdecomp")
NEAR = 0.06  # a near miss: at most this much of the function differs (best_score <= 6% of n_insns * 100)
SECONDS = 60  # per function per turn
MAX_TURNS = 4  # turns per function before it's left to agents (counted in the database: functions.permuted)
CANDIDATES = 20  # near misses looked up at a time, for the workers to share
TOOL = "decomp-permuter"
GENERATOR = Path(__file__).resolve().parent.parent / "permuter_gen.py"
BATCH = 64  # variants per run of the generator: about 0.15 s to write, a few seconds to judge
GENERATOR_SECONDS = 30  # a batch that takes longer is stuck: its randomizer retries until a rewrite applies
READ_BYTES = 1 << 16
MAX_SEED = 2**31  # seeds of the generator's runs: the same function and turn write the same variants
REWRITE_MISSES = 200  # simple rewrites in a row that change nothing: none applies to the C
MAX_INSNS = 400  # default size ceiling; larger functions cost more time per variant
REPAIR_MAX_INSNS = 1024  # deterministic hypotheses keep their own 32-attempt / 20-second cap
IDLE_SECONDS = 180  # with no near misses, how long until the permuter looks again
IDLE_LOOKS = 20  # looks in a row with nothing to do (an hour) and it's done until agents start again
HOLD_SPARE_SECONDS = 120  # a turn's hold on a function lasts this much longer than the turn, in case it runs over
_EOF = object()
# decomp-permuter's weights per compiler (its default_weights.toml), by the start of a toolchain's id
FAMILIES = (("ido", "ido"), ("gcc", "gcc"), ("ee-gcc", "gcc"), ("psp-gcc", "gcc"), ("mwc", "mwcc"))


def weight_family(cfg: dict, vram: int | None, fork: bool) -> str:
    """Which of decomp-permuter's weight sets suits the compiler that builds the code at `vram`."""
    if fork:  # forks name their console, not a toolchain: the console says its compilers' family
        return consoles.get(cfg.get("platform")).compiler_family or "base"
    toolchain = (compiler_for(cfg, vram).get("toolchain") or "") if cfg.get("compiler") else ""
    return toolchains.compiler_family(toolchain) or next((family for prefix, family in FAMILIES if toolchain.startswith(prefix)), "base")


def available() -> bool:
    return toolchains.installed(TOOL) is not None


def generator_argv() -> list[str]:
    tool = toolchains.path(TOOL)
    return [str(toolchains.venv_bin(tool / "venv", "python")), "-I", str(GENERATOR), str(tool)]


class Generator:
    """The generator (permuter_gen.py) for one turn, in decomp-permuter's environment. It stays up for the turn, so
    it parses the header once per C it starts from: that takes a second for a header of 300 KB (debug symbols give a
    game thousands of types), where writing a batch of 64 variants takes 0.1 s."""

    def __init__(self, argv: list[str] | None = None):
        self.proc = subprocess.Popen(argv or generator_argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=NO_WINDOW | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
        # Windows select() accepts sockets only. A reader thread also gives pipe reads a deadline there.
        self._lines = queue.Queue()
        threading.Thread(target=self._read, daemon=True, name="permuter-output").start()

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                self._lines.put(line)
        finally:
            self._lines.put(_EOF)

    def batch(self, job: dict) -> list[str] | None:
        """Variants of a function's C (the job's fields: permuter_gen.py), or None when the generator can't read
        the C, has stopped or doesn't answer: then the simple rewrites take over."""
        try:
            self.proc.stdin.write(json.dumps(job).encode() + b"\n")
            self.proc.stdin.flush()
        except OSError:
            return None
        deadline = time.monotonic() + GENERATOR_SECONDS
        variants: list[str] | None = []
        while True:
            line = self._line(deadline)
            if line is None or line is _EOF:
                if line is _EOF:
                    try:
                        self.proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                log.info("permuter: the generator stopped or hung on %s", job["function"])
                self.close()
                return None
            try:
                out = json.loads(line)
            except ValueError:
                continue
            if not isinstance(out, dict):
                continue
            if "done" in out:
                return variants
            if "error" in out:  # read on to its "done": the next job's answers start after it
                log.info("permuter: decomp-permuter can't read %s: %s", job["function"], str(out["error"])[:300])
                variants = None
            elif variants is not None and isinstance(out.get("variant"), str):
                variants.append(out["variant"])
    def _line(self, deadline: float):
        """The generator's next line, or None when it has stopped or hasn't answered by `deadline`."""
        try:
            return self._lines.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            return None

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


@dataclass
class State:
    state: str = "running"
    tried: int = 0  # functions permuted
    variants: int = 0
    improved: int = 0
    matched: int = 0
    current: str = ""  # the functions the workers hold now
    started_at: float = field(default_factory=time.time)
    stop: threading.Event = field(default_factory=threading.Event)
    repairs: int = 0  # deterministic candidates, with a separate per-round budget
    _held: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, **counts: int) -> None:
        with self._lock:  # every worker counts here
            for name, n in counts.items():
                setattr(self, name, getattr(self, name) + n)

    def holding(self, name: str, held: bool) -> None:
        with self._lock:
            if held:
                self._held.append(name)
            else:
                self._held.remove(name)
            self.current = ", ".join(self._held)


class Permuter:
    def __init__(self, net):
        self.net = net
        self.jobs: dict[str, State] = {}
        self.max_insns, self.seconds, self.max_turns = MAX_INSNS, SECONDS, MAX_TURNS

    def configure(self, settings: dict) -> None:
        """Global random-permutation budgets; running turns keep their existing deadline."""
        self.max_insns = max(1, min(100000, int(settings.get('permuter_max_insns', MAX_INSNS))))
        self.seconds = max(1, min(3600, int(settings.get('permuter_seconds', SECONDS))))
        self.max_turns = max(1, min(1000, int(settings.get('permuter_max_turns', MAX_TURNS))))

    def status(self, slug: str) -> dict | None:
        st = self.jobs.get(slug)
        return None if st is None else {k: v for k, v in st.__dict__.items() if k != "stop" and not k.startswith("_")}

    def start(self, slug: str) -> State | None:
        """Run on the project's near misses until there are none (looking again every few minutes while the
        app runs: agents make new ones)."""
        if (st := self.jobs.get(slug)) is not None and st.state == "running":
            return st
        if slug in self.net.links:  # a room member: the host's copy permutes, and its matches come back here
            return None
        st = self.jobs[slug] = State()
        threading.Thread(target=self._loop, args=(slug, st), daemon=True, name=f"permuter-{slug}").start()
        return st

    def stop(self, slug: str) -> None:
        if (st := self.jobs.get(slug)) is not None:
            st.stop.set()

    def candidates(self, proj, limit: int = CANDIDATES) -> list:
        return self.net.store.all(
            "SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
            "AND best_score > 0 AND best_score <= n_insns * 100 * ? AND n_insns <= ? AND permuted < ? "
            "AND id NOT IN (SELECT function_id FROM claims) ORDER BY CAST(best_score AS REAL) / (n_insns * 100) LIMIT ?",
            proj["id"], NEAR, self.max_insns, self.max_turns, limit)

    def _loop(self, slug: str, st: State) -> None:
        agent, _ = self.net.local_agent("permuter")
        idle = 0
        try:
            while not st.stop.is_set():
                try:
                    proj = self.net.project_row(slug)
                except NetworkError:
                    return  # the project is gone
                self._repair_round(agent, proj, st)
                todo = self.candidates(proj)
                if not todo:
                    idle += 1
                    if idle > IDLE_LOOKS:
                        return
                    st.stop.wait(IDLE_SECONDS)
                    continue
                idle = 0
                wait([workers.pool().submit(self._turn, agent, proj, fn, st) for fn in todo])
        finally:
            st.state = "stopped" if st.stop.is_set() else "done"

    def repair_pass(self, slug: str, stop: threading.Event) -> State:
        """Autopilot's Run again can try these without starting agents or random permutation."""
        st = State(stop=stop)
        if slug not in self.net.links:
            agent, _ = self.net.local_agent("permuter")
            self._repair_round(agent, self.net.project_row(slug), st)
        return st

    def _repair_round(self, agent, proj, st: State, *, function_id=None, budget=32, seconds=20) -> None:
        """At most 32 compiles / 20 seconds before normal random turns; rotate even over ineligible drafts.

        Cache individual candidates with their target, header and configured compiler. A new source/header/
        configuration gets another chance. This uses the same judge for ROM and imported project modes.
        """
        key = f"draft-repair-cursor:{proj['id']}"
        saved = self.net.store.one("SELECT value FROM settings WHERE key=?", key)
        cursor = int(saved['value']) if saved else 0
        todo = self.net.store.all(
            "SELECT * FROM functions WHERE project_id=? AND status='open' AND best_source IS NOT NULL "
            "AND best_score>0 AND n_insns<=? AND (? IS NULL OR id=?) AND id NOT IN (SELECT function_id FROM claims) "
            "ORDER BY (id<=?), id LIMIT ?", proj['id'], REPAIR_MAX_INSNS, function_id, function_id, cursor, CANDIDATES)
        deadline, attempts = time.monotonic() + seconds, 0
        fork = self.net.fork_slug(proj['slug'])
        symbols = [tuple(row) for row in self.net.store.all(
            "SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name", proj['id'])]
        for fn in todo:
            if st.stop.is_set() or attempts >= budget or time.monotonic() >= deadline:
                break
            with self.net.store.tx() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, str(fn['id'])))
            labels = {}
            candidates = draft_repairs.variants(fn['best_source'], fn['name'], proj['arch'], fn['asm'] or '', flags=compiler_for(self.net.cfg(proj),fn['vram']).get('flags',[]) if not fork else [], labels=labels)
            from . import repair_ranking
            ranked = repair_ranking.order(dict.fromkeys(labels.values()),repair_ranking.evidence(self.net,fn['id'],agent.id))
            from . import agent_tokens
            effect_scope=agent_tokens.target_scope(self.net,fn,'') if hasattr(self.net,'_context') else None
            ranked=repair_ranking.demote(self.net,effect_scope,ranked)
            candidates.sort(key=lambda text: ranked.index(labels[text]) if labels.get(text) in ranked else len(ranked))
            if not self._hold(agent, fn, count_turn=False):
                continue
            st.holding(fn['name'], True)
            try:
                current = self.net.store.one("SELECT * FROM functions WHERE id=?", fn['id'])
                if current['status'] != 'open' or current['best_source'] != fn['best_source']:
                    continue
                header = (self.net.forks.permuter_header(fork, fn['name']) if fork else
                          self.net._context(proj['id'], fn['unit'])['header'])
                # Imported builds own flags outside config. Their cache is only for this process/session;
                # the next explicit Run again/start retries after a user has changed their build setup.
                scope = st.started_at if fork else None
                context = repr((scope, header, self.net.cfg(proj), symbols, proj['arch'], proj['big_endian'],
                                fn['unit'], fn['target'], fn['relocs'], fn['vram']))
                best = fn['best_score']
                for candidate in candidates:
                    if st.stop.is_set() or attempts >= budget or time.monotonic() >= deadline:
                        break
                    digest = hashlib.sha256((context + candidate).encode()).hexdigest()
                    trial = f"draft-repair-v15:{fn['id']}:{digest}"
                    if self.net.store.one("SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?", fn['id'], trial):
                        continue
                    attempts += 1
                    st.add(variants=1, repairs=1)
                    baseline=getattr(self.net,'_agent_outcomes',{}).get((agent.id,fn['id']))
                    if baseline and baseline.get('source')!=fn['best_source']:
                        baseline=None
                    with repair_trial({'kind':'deterministic draft repair','family':labels.get(candidate,'draft_repair'),'base_source_sha256':hashlib.sha256(fn['best_source'].encode()).hexdigest(),'source_sha256':hashlib.sha256(candidate.encode()).hexdigest()}):
                        score = self._score(agent, proj, fn, candidate, best, fork)
                    measured=getattr(self.net,'_agent_outcomes',{}).get((agent.id,fn['id']))
                    if measured and measured.get('source')==candidate:
                        repair_ranking.remember(self.net,effect_scope,labels.get(candidate,'draft_repair'),baseline,measured)
                    with self.net.store.tx() as db:
                        db.execute("INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)",
                                   (fn['id'], trial, str(score), "targeted draft repair"))
                    if score is not None and score < best:
                        st.add(improved=1)
                        best = score
                        if score == 0:
                            st.add(matched=1)
                            break
                if function_id is None and not fork and best > 0 and attempts <= budget-3 and time.monotonic() < deadline and not st.stop.is_set():
                    attempts += self._repair_flags(agent, proj, fn, st, deadline)
            except Exception:
                log.exception("permuter: targeted repairs failed for %s", fn['name'])
            finally:
                st.holding(fn['name'], False)
                self._let_go(agent, fn)

    def _turn(self, agent, proj, fn, st: State) -> None:
        try:
            self._permute(agent, proj, fn, st)
        except NetworkError as e:  # the project refused its source (a helper the file hasn't): an answer, not a crash
            log.info("permuter: %s: %s (left to agents)", fn["name"], str(e).splitlines()[0])
            with self.net.store.tx() as db:  # the same best attempt is refused every time: no more turns on it
                db.execute("UPDATE functions SET permuted=MAX(permuted, ?) WHERE id=?", (self.max_turns, fn["id"]))
        except Exception as e:  # one odd function must not stop the rest
            log.info("permuter: %s: %s", fn["name"], e, exc_info=True)

    def _hold(self, agent, fn, *, count_turn=True, seconds=None) -> bool:
        now = time.time()
        with self.net.store.tx() as db:
            self.net._expire_claims(db)
            if db.execute("SELECT 1 FROM claims WHERE function_id=?", (fn["id"],)).fetchone():
                return False
            db.execute("INSERT INTO claims VALUES(?,?,?,?)", (fn["id"], agent.id, now, now + (self.seconds if seconds is None else seconds) + HOLD_SPARE_SECONDS))
            if count_turn:
                db.execute("UPDATE functions SET permuted = permuted + 1 WHERE id=?", (fn["id"],))
        return True

    def _let_go(self, agent, fn) -> None:
        with self.net.store.tx() as db:
            db.execute("DELETE FROM claims WHERE function_id=? AND agent_id=?", (fn["id"], agent.id))

    def _permute(self, agent, proj, fn, st: State) -> None:
        seconds = self.seconds
        if st.stop.is_set() or not self._hold(agent, fn, seconds=seconds):
            return
        st.add(tried=1)
        st.holding(fn["name"], True)
        fork = self.net.fork_slug(proj["slug"])
        rng = random.Random(f"{fn['id']}-{fn['permuted']}")
        gen = None
        try:
            if available():
                # (as m2c reads it: what's only for C++ made pycparser refuse GTA San Andreas's whole header)
                header = seeds.c_only(self.net.forks.permuter_header(fork, fn["name"]) if fork else
                                      self.net._context(proj["id"], fn["unit"])["header"])
                family = weight_family(self.net.cfg(proj), fn["vram"], bool(fork))
                try:
                    gen = Generator()
                except OSError as e:  # its environment is broken: the simple rewrites still work
                    log.info("permuter: decomp-permuter didn't start: %s", e)
            # (a draft still in m2c's macros isn't C decomp-permuter's parser reads: written out, it is the same code)
            start_src = best_src = cur_src = m2cfix.expand_macros(fn["best_source"])
            best = cur = fn["best_score"]
            deadline = time.monotonic() + seconds
            batches = misses = 0
            while time.monotonic() < deadline and best > 0 and not st.stop.is_set():
                if gen is not None:
                    # a batch of decomp-permuter's variants, of the C the turn started from and of the best so far in
                    # turn: from the best alone, rewrites piled on rewrites and got stuck on one of nine near misses of
                    # the demo ROM (at 45); taking turns, every one matched within 12 s
                    base = best_src if batches % 2 else start_src
                    batches += 1
                    batch = gen.batch({"header": header, "source": base, "function": fn["name"], "family": family,
                                       "seed": rng.randrange(MAX_SEED), "count": BATCH})
                    if batch is None:  # C it can't read: the simple rewrites can still try
                        gen.close()
                        gen = None
                        continue
                    if not batch:  # nothing new left to write from this C
                        break
                else:  # one simple rewrite, mostly of the last C kept
                    batch = [mutate(cur_src if rng.random() < 0.85 else best_src, rng)]
                    # none of them applies to this C (lerp's `a + (b - a) * t`): a minute of trying burnt a core
                    misses = 0 if batch[0] else misses + 1
                    if misses >= REWRITE_MISSES:
                        break
                for cand in batch:
                    if cand is None or time.monotonic() >= deadline or st.stop.is_set():
                        break
                    cand = m2cfix.static_helpers(cand)  # (a global helper would not link: m2cfix.py)
                    st.add(variants=1)
                    score = self._score(agent, proj, fn, cand, best, fork)
                    if score is None:
                        continue
                    if score < best:
                        best, best_src = score, cand
                        st.add(improved=1)
                        if score == 0:
                            st.add(matched=1)
                            log.info("permuter: %s matched", fn["name"])
                            break
                    if score <= cur or rng.random() < 0.02:  # mostly downhill, now and then sideways off a plateau
                        cur, cur_src = score, cand
        finally:
            if gen is not None:
                gen.close()
            st.holding(fn["name"], False)
            self._let_go(agent, fn)

    def _repair_flags(self, agent, proj, fn, st, deadline):
        """Two cached debug-flag probes on close ROM misses; persist only a byte-exact function range.

        Imported projects own their build flags: never rewrite their build setup from this phase.
        """
        if not hasattr(self.net, '_judge_with'):
            return 0
        current = self.net.store.one("SELECT * FROM functions WHERE id=?", fn['id'])
        if current['status'] != 'open' or not current['best_score'] or current['best_score'] > current['n_insns'] * 2:
            return 0
        own = compiler_for(self.net.cfg(proj), fn['vram'])
        ctx = self.net._context(proj['id'], fn['unit'])
        count = 0
        symbols = [tuple(row) for row in self.net.store.all('SELECT name,address FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
        choices = [f for f in self.net.debug_flag_variants(own['toolchain'], list(own['flags'])) if f != own['flags']]
        for flags in choices[:2]:
            if st.stop.is_set() or time.monotonic() >= deadline:
                break
            digest = hashlib.sha256(repr((current['target'], current['relocs'], current['best_source'],
                                          ctx['header'], self.net.cfg(proj), symbols, flags)).encode()).hexdigest()
            key = 'draft-debug-v1:' + digest
            if self.net.store.one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], key):
                continue
            count += 1
            st.add(variants=1, repairs=1)
            exact, _, _ = self.net._judge_with(proj, current, own['toolchain'], flags, current['best_source'])
            with self.net.store.tx() as db:
                db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                           (fn['id'], key, 'match' if exact else 'miss', 'near-miss debug flag probe'))
            if exact:
                # Changing this function's compiler settings cannot invalidate another function's match.
                self.net.set_compiler_ranges(proj['slug'], [[fn['vram'], fn['vram'] + fn['size']]], own['toolchain'], flags)
                fresh = self.net.project_row(proj['slug'])
                count += 1
                st.add(variants=1, repairs=1)
                if self._score(agent, fresh, current, current['best_source'], current['best_score'], None) == 0:
                    st.add(improved=1, matched=1)
                break
        return count

    def _score(self, agent, proj, fn, source: str, best: int, fork: str | None) -> int | None:
        """A variant's score; recorded (as the function's best attempt, or its match) only when it beats `best`."""
        if fork:
            res = self.net.forks.attempt(agent, fork, fn["name"], source, keep=False, quiet=best)
            return res.get("score")
        ctx = self.net._context(proj["id"], fn["unit"])
        result = self.net._compile(proj, ctx["header"], source, fn)
        if not result.ok:
            return None
        cand, verdict, _ = self.net._evaluate(fn, proj, result.obj)
        if cand is None:
            return None
        d = scoring.diff(self.net._target_obj(fn, proj), cand)
        if verdict is not None:
            d = scoring.DiffResult(0 if verdict else max(1, d.score), d.max_score, d.rows, verdict)
        if d.score < best:
            self.net._record(fn, agent, d.score, source, ctx["version"], matched=d.bytes_match, via="permuter", effective_settings=getattr(result,"effective_settings",None))
        return 0 if d.bytes_match else max(1, d.score)
