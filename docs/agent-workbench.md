# Agent queues, limits and model comparisons

## Preview the next work

Each agent card has **Preview queue**. It uses the same query as claiming and shows
up to 20 upcoming functions, its current claim, and exclusion counts with examples.
Matched/set-aside functions, active claims, stalled candidates, the worker's try
limit, match percentage, recent releases and recorded model history are explained.
SDK code is deferred in automatic mode, not forbidden. Starred work stays first in
automatic mode. A preview never claims, releases or expires anything. It is a
snapshot: other workers can change availability immediately. Joined-room members must inspect the host app’s queue; a local replica cannot predict its authoritative scheduler. Random order can
naturally change between requests; the preview examines at most 1,000 eligible rows.

## Bound a worker's allowance

Set **Spending limits & local compiler checks** in the launcher before starting,
or **Spending limits & compiler checks** on an existing card. Limits are per worker:
reported tokens, elapsed seconds, reported US dollars, and seconds without an
improvement. Blank/zero disables a limit. Limits are checked about every two seconds
and when new usage arrives. The first reached limit stops the worker, releases its
claim and prevents another session. Saving controls begins a new limit period;
manually starting it again does too. Display metric resets do not reset limits.

Token/dollar limits rely on provider reports. Some CLIs report only at session end;
interrupted calls may never report. A call can overshoot a limit, and a dollar-only
limit cannot bound a provider that never reports dollars. Use an elapsed-time cap
too. Dollar caps use the provider/CLI’s reported counter, not the app’s locally computed API-equivalent estimate. A reported CLI figure can itself be API-equivalent rather than a subscription charge. Joined-room members use time/usage limits; no-improvement timers require the host’s authoritative history.

**Compiler comparison not run first** is a queue strategy. **Before each session,
compare installed compilers** optionally checks the next queued function that has
saved C. Current cached results are skipped; changed inputs get checked again.
Checks use the existing installed-compiler judge and do not change settings. A
function with no saved C cannot be compared yet, and later functions in a session
are not automatically preflighted. Imported projects keep their own compiler tools:
full installed-compiler comparison remains a documented project-mode gap.

## Understand a difficult function

In the function inspector, **Model attempts** shows checks, improvements, best
percentage and verified verdicts per recorded model. Unknown attribution stays
separate. The token and reported-dollar columns are labelled **project totals**:
we cannot allocate those costs reliably to a single function. Effort variants keep
the session's recorded identity here.

**Explain remaining differences** compiles the best saved candidate using the
existing diff path. It groups observed markers: register allocation, stack offsets,
reordered instructions, operands/constants, different, missing or extra instructions.
Each group links to its supporting instructions. These are observations and possible
next checks, not proven source-level causes. The byte judge is unchanged.

Agent cards and model cost summaries show verified code bytes and verified bytes
per million reported tokens (including caches). Unknown/zero usage has no ratio.
Card counters follow their reset baseline; model totals remain lifetime totals.
Model byte attribution requires a matching owner and a recorded session at the
match timestamp. Missing historical attribution is not guessed.

## Run models from one checkpoint

Open **Agents → Compare models**:

1. Stop the source project's agents and wait for active checks to finish.
2. **Create checkpoint** copies the project and takes a consistent SQLite backup.
   Changes during copying invalidate it. The copy excludes Git pointers and other
   projects; old helpers and room/settings state cannot resume in a trial.
3. Choose two to six explicit model IDs and configured API endpoints when needed.
   Choose one shared deterministic queue order (easiest first by default) and an equal elapsed-time and/or reported-token budget. Adaptive routing and model-specific history filters are disabled in controlled trials. Token-only requests
   also receive a finite 30-minute wall-clock ceiling. The UI defaults to 30 minutes.
4. Stop the app’s other agents to avoid competing provider slots. New/restarted helpers are blocked while a controlled comparison runs. Confirm the account/allowance use and press **Start comparison**. No model starts
   merely by opening this panel or creating a checkpoint.
5. Review new/exclusive matches, distinct improvements, recovered bytes, elapsed
   time, reported usage, reported cost and API-equivalent estimates. Earlier reports
   remain accessible. Trial copies and their local logs remain under
   `.decomp/comparisons/<checkpoint>/runs/` for inspection.

Trials run sequentially in separate processes. Each gets a fresh copy of the same
checkpoint, an independent database and private local MCP server with the normal
project tools. Automatic twins/permuter/reuse work is disabled so it cannot silently
inflate a model's result. Provider caches, model nondeterminism and other machine
work can still affect results. Hashes detect changed checkpoints before running.

For imported projects, explicitly allow their copied build scripts. Files are
materialized and project paths relocated, but this is **not an OS filesystem sandbox**:
custom scripts run with your account permissions and external dependencies may need
setup in the copy. A build/auth failure is reported as a failed trial, not a zero-cost
success. Hosted-room projects must leave the room before checkpointing.

Closing the panel leaves a comparison running; **Stop comparison** cancels it.
Application shutdown requests cancellation and waits for workers to stop. Trials
never merge automatically. Inspect and verify any results before transferring them
into the main project. A restarted app labels abandoned runs interrupted and never
silently resumes paid work. Checks and gains are measured against the checkpoint's
original unchanged targets; newly split/changed targets are excluded and disclosed. Regressions in previously matched checkpoint functions mark a trial failed.

## Validation boundaries

Portable tests cover both queue modes, caps, observed diffs, attribution, checkpoint
integrity, copies/relocation, cancellation, consent, and a real local worker/MCP
lifecycle with a deterministic stand-in provider. Compiler preflights use the existing
exact judge. Live paid model trials are deliberately not part of tests. Physical
Linux/Windows process behavior is left to native CI and host validation.
