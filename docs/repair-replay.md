# Replay captured repair examples

## In the app

Open **Agents → Repair examples** and use **Test automatic repairs**. It tests up to 20 examples
matching the category/review/project filters. Each expanded card also has a single-example action.
Choose 1–32 trials and 1–120 seconds per example; defaults are 12 trials / 30 seconds. Progress and
results appear in the dialog; **Stop replay** cancels compiler work through the shared job system.
Results are compiler-output regressions, not adopted game matches, and do not mark examples implemented.
Imported projects or incomplete historical context are reported as skips.

## Offline

Use replay to find out whether the app's current deterministic families can reproduce a captured repair.
It uses no model requests, opens no project database and does not apply candidates or change review states.

1. In **Agents → Repair examples**, select the cases to investigate and **Export full examples**.
2. Keep this private export outside the repository: it contains game C and headers.
3. From a checkout, run:

```sh
uv run python -m decompnet.app.repair_replay /path/to/repair-examples.json \
  --output /path/to/replay-results.json --limit 20 --trials 24 --seconds 120
```

Use `--ids 123 456` to select specific cases. Limits are per example; baseline/control compilation and
the current trial can finish after the soft deadline, with normal compiler subprocess timeouts still active.
No toolchains are downloaded. Install the captured compiler through the app first if necessary.

The report checks source/header hashes, identical captured settings, and a reproducible baseline/control
difference. Missing historical C or context, changed settings and uninstalled tools are reported as skips.
Candidate families rotate, one candidate per family per round, so a large family cannot spend every trial.

Results include `compile_error`, `improved`, `neutral`, `regressed`, and `control_output_recovered`.
The last means the normalized function fingerprint, all allocated sections (including helper code and BSS sizes), and named relocations equal the compiled
successful control. It is **not a newly verified game match**: the export does not carry a complete ROM memory
map or an imported project's build and objdiff objects. Debug information is not part of this comparison.
Use the original project's normal linked-byte/objdiff judge on a copy before adopting a proposed fix.
Without edit minimization the report stores hashes and bounded diagnostics. Enabling minimization also includes the resulting private C.

The shared runner also accepts a diagnostic measurement callback for copied project integrations.
Standalone imported-project replay currently skips cases because full exports do not capture the complete
build system and historical headers; a guessed compiler profile would give misleading results.

## Current evidence

Random-rewrite labels now validate the recorded candidate hash. A label inherited while a successful
candidate triggers more matches is retained as origin information, not treated as the destination's
rewrite. Reports additionally distinguish whether the recorded base hash equals the failed baseline.
The last rewrite name is not sufficient to isolate the change that caused a match.

A private audit found eight of 444 random-labelled captures with matching candidate hashes; only two
also had the recorded failed baseline. One was already recovered by `table_slot_lifetime`; the other
motivated bounded `member_store_replay` proposals. These are snapshot counts, not an estimate of model
or permuter effectiveness. Existing capture/review records were not deleted or reclassified.

Four private captured regressions were recovered by `pointer_chase_reuse`,
`independent_assignment_order`, `terminal_early_return`, and `member_store_replay`. Separate normal
linked-byte checks accepted a generated candidate in each case. The first three used KMC GCC 2.7.2;
the member-store case used IDO 5.3. These recover existing matches, not additional game progress.

A private same-context floating-point case used KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The product-intermediate family recovered the successful control;
two alternatives improved regression score 180 to 35 and 55. A separate normal linked-byte check in a
private project copy accepted one candidate exactly. This recovered an existing match, not new game progress.

Three other private cases produced no control recovery in a 12-trial allowance (one had only nine
applicable candidates). These neutral outcomes are retained so wider search is evidence-led.

## Minimize a captured fix and inspect GCC phases

The Repair examples replay panel now includes:

- **Minimize captured fix first**, checked by default. Up to half the existing trial budget reverts
  individual changed line hunks. An edit is removed only if the successful function output and all
  allocated object sections/relocations still agree. Earlier removals are retained while later hunks
  are tested. This is bounded ablation, not a proof of semantic necessity or a globally minimal fix.
- **Capture GCC phase summary**, off by default. At most two additional local compiles add `-da` to
  the captured failed and successful sources. This requires an already installed native GCC driver;
  unsupported compilers/adapters are explicit skips, and nothing is downloaded. Each diagnostic output
  is compared with its normal compile before register/phase evidence is considered comparable.
- **Download analysis report** after a run. The local report includes the minimized C, retained edit
  indexes, per-trial outcomes, and bounded phase summaries. Treat it as private game-source evidence.

The phase summaries list captured phases, pseudo counts, reference/lifetime/call-crossing statistics, pseudos disappearing between flow and
combination, empty conflict lists, selected allocations and truncation markers. These are investigation
leads, not proof of why a spill exists. Full raw dumps are not retained or sent to an AI service.
The diagnostic compiles share cancellation and compiler resource limits; they can consume the remaining
replay time. Analysis never applies C, changes compiler settings or marks a game function matched.

The same controls are available offline:

```sh
uv run python -m decompnet.app.repair_replay /private/repair-examples.json \
  --output /private/analysis.json --trials 24 --seconds 120 --minimize --diagnostics
```

Scalar failed-source penalties are now scoped to the exact parent C as well as its build inputs.
Changing source permits relevant families to be explored again. Exact failed compiled candidates remain
cached under their existing build context: retrying a family does not mean recompiling identical input.
