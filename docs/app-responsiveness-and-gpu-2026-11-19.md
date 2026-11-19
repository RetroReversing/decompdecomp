# App responsiveness, deep-lane sizing, and the "busy" AMD GPU

Date: 2026-11-19. Scope: the mrppp/decompdecomp AppImage on this machine (BEAST).

This records four separate problems that all looked like one ("the app is slow"), what
each really was, how it was fixed, and the numbers that show it. It is written so the
checks can be re-run without re-deriving them.

## Summary

| symptom | real cause | fix | effect |
|---|---|---|---|
| every project stuck at "loading additional project details" | the Xbox 360 compiler lookup re-walked the XDK folder tree for every file | `kits.compilers` kept between calls, cleared when Setup changes | 10,133,011 `iterdir` calls -> 517; overview 150 s+ -> 0.8-1.7 s |
| a big project's page blocked others | the live overview was built inside the poll, holding one of the app's two live-lane slots for minutes | build in a daemon thread, answer with the cheap overview marked `overview_pending`, back off after a failure, adaptive TTL | first LEGO poll 0.03 s, real counts 1.5 s later |
| load average 25, `/api/state` 1-3 s | deep lanes sized by `os.cpu_count()` (12) while the process may use 8, and a sweep that ended while lanes were mid-turn never closed them | size by the affinity mask (`cores.usable_cores()`), close every lane in `Lanes.close()`, refuse to start after close | 19 lanes -> 6; `/api/state` 1-3 s -> 0.0 s; stopped sweep leaves 0 processes |
| the AMD GPU "kept busy" with nothing running | no inference at all: the app's own agent helpers poll `/api/tags`; a probe-loaded model then sat in VRAM under the keep-alive | keep-alive already 2 m (`override.conf`); model unloaded | `/api/ps` empty; GPU 4-11% is desktop compositing |

## 1. Per-file folder walks in the XDK lookup

Profiling the real `overview()` call on a copy of the LEGO project's databases (38,694
functions) showed the cost was not SQL:

- `xdk360.compilers` called 18,889 times per overview
- `kits.find` 129,686 times, producing 10,133,011 `pathlib.iterdir` calls
- 368,640 `posix.listdir` syscalls, 65 s in `listdir` alone

`xdk360_file_compiler` -> `selected_xdk360_compiler()` -> `kits.compilers()` -> `Kit.locate()`
proved the kit was still where Setup recorded it, on every call, by listing each folder
level (case-insensitive lookup lists the directory).

Fix (`18c229b5`): `kits.compilers(kids)` is memoised by the tuple of kit ids, and
`kits.forget()` clears it - called from `toolchains._write_copies` and `remove_local`,
which are the only paths that change where a copy is. Caching `find`/`folders` instead was
rejected: it also caches "not there yet", which breaks a user who adds files inside a kit.

Measured on the same database: overview 0.83-1.7 s, 517 `find` calls, correct counts
(38,694 functions, 2 matched at the time).

Regression test: `tests/test_kits.py::test_the_compilers_of_the_kits_are_kept_between_calls`
(a second ask walks nothing).

## 2. The live overview blocking the app's lanes

The app's live lane has two slots. The overview for a big project was read inside the
poll, so two concurrent polls held both slots for minutes and every other project's page
sat at "loading additional project details".

Fix (`22797023`, `c73e0018`, `873c37d2`):

- the poll starts a daemon thread and answers at once with the cheap overview marked
  `overview_pending`;
- the worker re-reads the project row and its own thread-local store, re-checks the change
  key afterwards and discards a result the project changed under;
- a failing project backs off 15 s instead of spawning a thread per poll;
- a rebuild that took a while waits a multiple of its own duration (`max(TTL, cost * 5)`);
- above `LIVE_OVERVIEW_AUTO_MAX` (250,000 functions) no automatic summary is attempted at
  all - a guard for absurd sizes, not a routine limit.

Verified on the running app: LEGO first poll 0.03 s (`overview_pending: true`), real
counts 1.5 s later, `dash-dash` 0.03 s during that build.

Note: `permuter` in the live overview is the old in-process worker. A deep sweep reports
through `/api/permuter/pass`, so the live field reads `None` while a sweep runs. Not a bug,
but it misleads when checking status.

## 3. Deep-lane sizing and a lane leak

Observed: 19 `deep_lanes --child` processes at ~11.4 cores of demand on a box with 8
usable cores, load average 25, `/api/state` 1-3 s.

Two independent faults:

1. **Sizing used the wrong count.** `os.cpu_count()` returns 12; the process is pinned to
   8 (`taskset` shows 0,1,4-7,10,11). Every sizing path used the reported count.
2. **`Lanes.close()` only closed idle lanes.** It drained `self.idle`; a lane that was
   mid-turn was never closed, so a sweep that ended left its lanes running for the life of
   the app. That is where the second generation (nine 7-minute-old lanes beside ten
   4-minute-old ones) came from.

Fix (`c6d166cc`):

- `src/decompnet/cores.py::usable_cores()` reads the affinity mask, falling back to
  `os.cpu_count()` where there is none (Windows, macOS);
- workers, `compiler.concurrency_limit()`, `deep_throughput.width()` and each lane's
  compile share all use it;
- `Lanes` records every child it starts, `close()` closes all of them and marks the pool
  closed, `_take` refuses to start or hand out after that, and a lane returning from a
  turn closes itself if the pool is shut.

On this machine: 6 workers (was 10), 7 compile slots (was 11), 8 maximum width (was 12).
Verified live: `threads_default: 6`; a 1-minute pass ran exactly 6 lanes; stopping it left
0 lane processes; pass state `cancelled`.

Tests: `tests/test_cores.py` (affinity, fallback, empty mask) and
`tests/test_deep_lanes.py::test_close_reaches_a_lane_that_is_mid_turn` (busy lane closed,
closed pool starts nothing).

### Mistake worth remembering

The first attempt at clearing "stale" lanes parsed `/proc/<pid>/stat` field 22 and
compared it with a 420-second cutoff. Field 22 is `starttime` in clock ticks since boot,
not elapsed time, so every process looked stale and 17 of 18 lanes were killed, including
the live sweep's. No matched work was lost - only in-flight candidates - but the correct
age is `now - (starttime / clock_ticks) - btime`, or simply `ps -o etime`.

## 4. The AMD GPU that looked busy

Checked because the box showed GPU load while nothing was obviously running.

Facts gathered:

- the AMD device (1002:7590) is on `amdgpu` on the host; `card1` reports
  `gpu_busy_percent` 4-11% at idle, 7.5 GB of 16 GB VRAM in use;
- ollama loads `qwen3.5:4b` (3.8 GB, 32k context) and its log shows **ROCm**, not the
  Quadro P2000: `ROCm0 KV buffer`, `CLIP using ROCm0 backend`;
- in ten minutes ollama served 183 `GET /api/tags`, 44 `GET /api/show`, 1 `GET /api/ps`,
  and **zero** `/api/generate`, `/api/chat` or `/api/embed` - no inference;
- the clients are the app's own helpers: eight `opencode serve` processes whose working
  directories are `.decomp/agents/opencode-hy4-flash-free-65..72`.

So the GPU was touched by model discovery and by a model left loaded under the keep-alive,
not by work. The remaining 4-11% is desktop compositing (Steam, Edge, Claude Desktop,
WebKit all hold GPU contexts).

Actions: `OLLAMA_KEEP_ALIVE` was already `2m` in
`/etc/systemd/system/ollama.service.d/override.conf` (a redundant 60 s drop-in was removed
again); the model is unloaded now and `/api/ps` is empty.

**Do not "clean up" those opencode processes.** They are live agent sessions, and
`opencode serve --service` (the long-running one) is the backend running the chat itself.
Killing them stops the user's agents and the conversation.

## How to re-check

```sh
# app health and overview cost
curl -s -H "Host: 127.0.0.1:$PORT" -H "User-Agent: decompdecomp-window/$SECRET" \
     "http://127.0.0.1:$PORT/api/state"                 # time this
curl -s ... "/api/live?slug=<slug>&compact=1"           # overview_pending, then real counts

# per-thread CPU, written every minute by the app
tail -30 ~/Decomp-RetroReversing/.decomp/logs/thread-cpu.log

# lane count and the machine's real width
pgrep -f 'deep_lanes --child' | wc -l
python3 -c "import os;print(os.cpu_count(), len(os.sched_getaffinity(0)))"

# ollama: inference or just discovery?
journalctl -u ollama --since '10 min ago' | grep -oE '"(/[a-z/]+)"' | sort | uniq -c
ollama ps
```

Commit hashes: `08bdde42` (objdiff rows), `22797023` (over-thread overview), `c73e0018`
(summary cap), `873c37d2` (TTL test), `18c229b5` (XDK lookup cache), `09ebe461` (remove
stopped agents), `937dfd2f` (upstream merge), `c6d166cc` (core sizing and lane close).

## State at hand-off (2026-11-19, ~11:40 UTC)

**Running**

- The AppImage (`1.0.9-rr.2`, built from `c6d166cc`) runs **detached** (`setsid nohup`), so a shell session
  ending cannot take it down. Its port and secret are in `~/Decomp-RetroReversing/.decomp/server.json`;
  `pgrep -f decompnet.app.cli` finds it.
- The permuter / deep sweep is **stopped** (deliberately, after the lane leak). The next start uses **6 lanes**
  on this machine.
- ollama: `OLLAMA_KEEP_ALIVE=2m` in `/etc/systemd/system/ollama.service.d/override.conf`, no model loaded.
  The app's helpers poll `/api/tags`; that is discovery, not inference.

**Projects**

- `dash-dash`: 70 matched of 18,819. Two near-misses were reopened when the lanes were killed
  (`FUN_9218ce48`, unit diff 95.00%; `FUN_9217fb20`, 99.85%). They are `open` and the deep pass retries them.
- `lego-dimensions-usa-europe-en-fr-de-es-n`: 38,694 functions, 12 matched; overview 0.8-1.7 s.

**Open items**

- Run the XDK survey on LEGO; it still builds with `xbox360-xsdk-placeholder`.
- Rename `orig/<version>.xex` for base-image projects and split manifest provenance
  (`source` / `extracted_xex` / `base_image` / `mapped_image`).
- Hash `xextool.exe` in the kit.
- The opencode helper DB is keyed per credential, which pins helpers to a stale key until their DBs are cleared.
- Known test residuals, neither new: `tests/test_kits.py` (2 failures) and `tests/test_fork_judge.py`
  (20 errors; this machine's git 2.17 has no `git init -b`).

**Next action**: restart the permuter on dash-dash (6 lanes) so the two reopened near-misses are retried, or
run the XDK survey on LEGO.
