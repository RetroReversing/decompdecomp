# Replay captured repair examples

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
The last means the normalized function fingerprint and allocated constant/data sections equal the compiled
successful control. It is **not a newly verified game match**: the export does not carry a complete ROM memory
map or an imported project's build and objdiff objects. Debug information is not part of this comparison.
Use the original project's normal linked-byte/objdiff judge on a copy before adopting a proposed fix.
The report stores hashes and bounded diagnostics, rather than another full source export.

The shared runner also accepts a diagnostic measurement callback for copied project integrations.
Standalone imported-project replay currently skips cases because full exports do not capture the complete
build system and historical headers; a guessed compiler profile would give misleading results.

## Current evidence

A private same-context floating-point case used KMC GCC 2.7.2 with
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. The product-intermediate family recovered the successful control;
two alternatives improved regression score 180 to 35 and 55. A separate normal linked-byte check in a
private project copy accepted one candidate exactly. This recovered an existing match, not new game progress.

Three other private cases produced no control recovery in a 12-trial allowance (one had only nine
applicable candidates). These neutral outcomes are retained so wider search is evidence-led.
