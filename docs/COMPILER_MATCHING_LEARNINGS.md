# Compiler matching learning log

Keep this file updated when a verified match explains a reusable compiler behaviour. Agent-facing
summaries live in `src/decompnet/consoles/*/guides/`; this is the evidence ledger, including failed ideas.
Do not treat an instruction similarity score as proof of a match.

## How to add a finding

Record the compiler **version and flags**, processor/ABI, original mismatch, source change, measured
result, evidence location, and remaining uncertainty. Distinguish a controlled same-input experiment
from a sequence of edits. Keep original line layout, headers, symbol addresses and target bytes fixed
unless that item is the variable being tested. Do not commit game data or compiler binaries here.

Use these evidence levels:

- **Verified case:** the normal judge accepted exact linked bytes (or objdiff 100% for an object project).
  A ROM publication also needs the complete image rebuild. One case is not a universal compiler rule.
- **Controlled regression:** undo one aspect of a known solution and check that a generated repair
  recovers it. This tests the generator; it is not an additional newly matched function.
- **Observation:** output changed, but the mechanism or all contributing edits are not isolated.
- **Hypothesis:** worth trying, with no successful controlled test yet.

## KMC GCC 2.7.2, 32-bit big-endian MIPS

### Verified case: a large rendering/menu function

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Evidence: [published matching source and verification notes](https://github.com/RetroReversing/v-rally-decomp/commit/31c03a37ad31e2f106df7df51c5380307ad08056).
Function `func_8006090C`: 883 instructions / 3,532 bytes. The normal app judge and standalone check
both accepted it; the 12,582,912-byte cartridge rebuilt identically, SHA1
`da3f262dfb86cff1376c9a1aa081212e5a7aad64`.

| Source change | What changed in the generated code | Evidence and limits |
| --- | --- | --- |
| Represent a 3-byte string copy with `__builtin_memcpy` | Recovered signed halfword/byte loads, store order and a cached destination address | Contributed to the final match. The source was a real 4-byte char array with `aligned(2)`; dropping alignment produced byte loads and worsened the score. Do not copy 3 bytes from a declared 2-byte scalar or assume adjacent locals form a buffer. |
| Recover record, actor, packet and graphics-command types | Recovered field addressing, alias information, load order and register allocation | Real sizes/offsets matter, including nested record tables. Candidate-local types were used; no shared layout was guessed globally. |
| Remove an artificial outer `do { ... } while (0)` | Recovered initial lookup and first-loop registers | Score 1425 → 1055 in that experiment. Scope/loop structure matters; do not remove a wrapper containing loop control blindly. |
| Merge the later UI pointer lifetime and time-accumulator lifetime together | Restored the intended saved registers | Score 1055 → 1010. One of these edits alone made allocation worse. Textual non-overlap is not sufficient proof of liveness across loops. |
| Use actual globals instead of invented offsets from a common base | Changed common-subexpression sharing and load scheduling | Several staged improvements; not all gains were isolated. An address name is not a substitute for the object's actual declaration. |
| Compute a positive field address, then derive the matrix base with a negative element offset | Recovered the shared base register and zero store | A score-480 alternative contained the desired addressing where the score-440 candidate did not. Reordering setup on the alternative led to score 210. Preserve promising alternatives even when total score regresses. |
| Use a small lexical block and local graphics-command pointer, with the actor read at the call | Recovered the final constant-load placement | Score 210 → 0. The block and actor-load change were coupled. This does not show that braces alone change every GCC build. |

### What did not help this case

- 300 declaration permutations were neutral; broad register qualifiers and local retyping mostly failed.
- Broad optimization, CPU and debug-flag comparisons did not beat the original pinned profile.
- 600 region permuter candidates did not improve the then-best score of 440.
- A five-minute earlier deep pass tested 325 candidates plus two cached misses with no improvement.
- Many matrix/union/volatile/zero-copy alternatives failed. Avoid repeating these families indefinitely
  on unchanged input; retain scoped diagnostics and cache outcomes with source/header/compiler keys.

### Automation derived from this case

`app/compiler_shape_repairs.py` proposes bounded short copies, existing typed field accesses, one-shot
loop/scope alternatives, pointer block scopes and shared array addresses. `app/draft_repairs.py` also
bundles two conservative scalar lifetime merges. These run through the existing draft/permuter paths,
not an AI API. They do not reconstruct arbitrary missing record layouts or prove source equivalence.

`app/iterative_repairs.py` reserves one beam slot for a candidate up to 25% worse than the branch's
best score. The ceiling cannot compound at successive depths. Phase budget, depth limits, output
fingerprint limits, cancellation and fair function rotation still apply. These alternatives live in
search state, not in the saved best source. Only the normal judge accepts a match.

Controlled private regressions of the published function exercise the generated repairs. These are
regression checks, not claims of extra game progress:

- Undoing both 3-byte copies scored 2675. The combined short-copy candidate recovered exact bytes;
  the individual-site candidates scored 1335 and 1580.
- Replacing typed actor accesses with raw offsets scored 210. The typed-record candidate recovered
  exact bytes.
- Isolating matrix-address and pointer-scope edits in the final solution did not lose the match;
  generated candidates stayed exact. These checks establish compatibility, not a measured improvement.

Single-function deep search also explores up to three bounded retained alternatives per local-repair burst under its
existing deadline/compile allowance. It returns the saved best source when the detour fails; only the
normal judge can save a later improvement. Persistent multi-function iterative state is separate.

### Verified follow-up: a 966-instruction menu function

Profile: `gcc-2.7.2-kmc`, `-G0 -mips3 -mgp32 -mfp32 -O2 -g3`.
Evidence: [published exact checkpoint](https://github.com/RetroReversing/v-rally-decomp/commit/4562473).
The normal app judge, independent standalone function check and complete 12,582,912-byte cartridge
comparison accepted the result. All previously selected C remained selected.

**Controlled final repair:** materializing a `u16` mask into an existing, subsequently reset 32-bit local
recovered the target's in-place saved-register mask. A fresh temporary or assignment back to the input
did not produce the same allocation. Reusing a non-overlapping local is a hypothesis to compile, not
proof of the original source's variable names or grouping. The final source also retains earlier
reconstruction choices; matching bytes do not establish portable source semantics or original spelling.

Other observations from the staged search:

- Associating a table displacement with its base before adding the index recovered address ordering.
- A small bitfield local in an inline helper changed frame reservation. Ordinary unused array padding
  shifted other locals and failed. This does not establish a general-purpose stack-padding idiom.
- A redundant conditional disappeared from the instructions but changed scheduling/allocation. Moving
  loop-counter initialization into an existing comma-expression recovered a call-delay-slot placement.
  These edits were coupled; do not attribute the entire gain to one predicate or one line break.
- Broad flag/type/declaration experiments were mostly neutral or worse. A controlled Linux-host versus
  macOS-host compiler comparison emitted identical text for the tested input. No host-specific fix was
  needed. Preserve the pinned profile, including `-g3`.
- The final private disassembly score remained 120 because 12 numeric-versus-symbolic relocation rows
  rendered differently. The linked-byte verdict was exact; the app correctly recorded score zero.
  Never use a heuristic score, normalized display or register similarity as the acceptance gate.

**Automation:** `masked_reuse_repairs.py` materializes a cast/mask into a local proven dead by the bounded
CFG in `local_flow.py`. Its graph includes if/else, loop back edges, switch fallthrough, break/continue
and direct gotos. Unknown syntax, escaped/qualified locals and unsupported control flow give no advice.
The same analysis supplies additional lifetime merge candidates and small m2c draft hints without
rewriting the default draft. These candidates share existing budgets and the unchanged judge.

Search classification resolves known MIPS relocation operands before choosing weights, without changing
the original rows or score. Deep/random search retains a bounded numeric/structural candidate frontier;
iterative repair retains the numeric winner alongside structural alternatives and its existing bounded
detour. New random improvements immediately seed a new batch; newly saved improvements are remeasured
with current headers/settings. Existing scoped compile caches remain in use.

**Private checkpoint replay:** the new masked-reuse family recovered exact linked bytes on its first
candidate from both the score-130 and score-155 checkpoints, in approximately 1.1–1.2 seconds including
a baseline compile on the test machine. The integrated deep local-repair phase also saved exact
matches from both checkpoints: two compiles including baseline, approximately 3.2–3.4 seconds
including proposal generation and bookkeeping. It did not finish the earlier score-180 checkpoint within its
24 generated candidates. These are retrospective local-family checks with an already selected compiler
profile, not fresh-start timing, extra progress or proof that the original 4h25m investigation now takes
one second. Game source/ROMs and private checkpoints are not included in the app repository; regression
fixtures here are synthetic.

## IDO 5.3 / 7.1, 32-bit big-endian MIPS

### Observation: source layout sensitivity (IDO 5.3)

The existing [IDO guide](../src/decompnet/consoles/n64/guides/ido.md) records a private same-source
comparison: preserved whitespace scored 0; replacing every whitespace run with a space scored 120
and changed call/load ordering. This establishes layout sensitivity for that input. It does **not**
isolate newlines alone or establish instruction scheduling as the mechanism.

A broader source-assisted pass gained 22 matches, but header/global declaration repairs also changed.
Do not attribute all 22 to line breaks. Preserve successful formatting; compare preprocessed tokens,
compiler/flags, headers, macros and linked instruction bytes before making stronger claims.

### Existing guide advice, not new controlled findings

Float/double literals, argument home slots, expression shape, global reloads and first assignments can
matter. IDO 5.3 and 7.1 are separate profiles; no result here establishes that a 5.3 observation applies
to 7.1. Record future tests under their exact version and flags.

## Other GCC families: SN64, EGCS and GNU GCC

These share some source idioms with KMC but use different compiler/assembler versions and profiles.
No new controlled successful case for these versions was established in this investigation. Do not
transfer the KMC score improvements or whitespace effects to them as measured facts. Record each
profile independently, including debug/assembler options and endianness.

## CodeWarrior, MSVC, ARM compilers and other installed profiles

No new per-compiler match evidence was collected in this investigation. Existing console guides remain
available; the architecture-neutral source hypotheses may be tested by a supported project's normal
judge. Unsupported builtins or incompatible types should fail compilation, not trigger a profile change.
Add a versioned subsection when a successful, reproducible case is available. Do not fill this ledger
with inferred compiler facts simply because a profile is selectable.

## Rules shared by every compiler

1. Preserve source plus context, flags, symbol map and verification evidence together.
2. Match machine bytes, not a percentage rounded to 100 or a disassembly with cosmetic relocation labels.
3. Local declarations, object bounds and alignment are part of the experiment. A cast cannot establish
   an object's size, and arbitrary padding/NOP tails must not inflate progress measurements.
4. Keep promising structural alternatives, bounded by explicit CPU budgets. A local gain can accompany
   a global register-allocation regression.
5. Log neutral and failed families too. Revisit them when their inputs or the repair rules change.
6. Rejudge after formatting or polishing. Preserve prior verified work and never weaken the target/judge.


## IDO 5.3: typed SDK context, structure returns and export layout

**Verified cases (32-bit big-endian MIPS, source assisted):** a recent six-match continuation used
`-mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`, retaining each function's selected `-O2` or `-O3`
profile. Native structure return replaced an explicit output pointer plus final record copy, recovering
the compiler's hidden result-pointer ABI. Typed auxiliary-bus layout, original command macros, argument
order, and inline word setters' pointer locals recovered other functions. A separate candidate needed
an unresolved global's ROM-derived address. The complete standalone cartridge was verified; these
observations do not identify the original IDO release uniquely or isolate every individual source edit.

**Controlled regression:** `tests/test_structure_return_repairs.py` compiles a homemade record-return
example with already-installed IDO 5.3 at `-O3 -mips2 -G 0 -non_shared -Xcpluscomm`; the generated native
return has the target object's exact normalized function fingerprint. This tests the repair generator,
not an additional game recovery. Typed inline-setter proposals have bounded source tests and still need
the destination's ordinary byte judge. No shared record layout is invented.

**Failed/neutral observations:** restoring the SDK bodies and line layout of two tiny sequence byte
readers still left temporary-register differences under `-O2`. Do not repeat that as an assumed fix.
A four-instruction thread-list helper also retained a temporary-register difference after restoring
its pointer return. Neither case was counted as a match.

**Integration evidence:** a function-level exact match can contain a string and numeric/jump-table
constants that need different bases within one object section. The case required referencing the
original string separately before the whole build matched. `tests/test_candidate_diagnostics.py`
checks non-contiguous placements and extra bodies without changing the function's exact verdict;
`tests/test_repository_sync.py` protects both expanded-image and original-cartridge hashes.
A successful local layout check is not proof of a full rebuild.
## Follow-up: adaptive priorities, region selection and inferred records

Profile-specific outcome learning now records fresh compiler trials by compiler/flags/ABI and mismatch
kind. It waits for 32 observations per family, smooths sparse results and clamps multipliers to
0.25–4. Random search retains alternating upstream-default batches; deterministic queues interleave
learned and original orders. Cached deep misses do not train twice. Old aggregate counts are not
silently assigned to current compiler settings: their historical profile/focus snapshots are absent.
Imported builds are isolated by their actual build-evidence scope rather than a guessed compiler.
This is a search policy, not evidence of a measured speedup yet.

Target-aligned 16-instruction regions now help choose the reserved worse-score branch. The original
883-instruction case was replayed with fixed compiler/context/target: the score-480 alternative
recovered three regional mismatches relative to the score-440 candidate. That is a useful partial
recovery, not an exact match; the previously verified final source still scores 0. Target stream
identity is required, so inserted candidate instructions cannot shift the comparison.

Candidate-local record inference now recognises multiple explicit byte-offset accesses, including
m2c's parenthesised forms. It requires at least three accesses to at least two consistent,
non-overlapping, aligned fields, with bounded offsets and no volatile/unknown types. The inferred
record is private to a source candidate. It does not assert the full object size, edit a shared header,
or propagate an unverified layout to other functions.

Private same-checkpoint test results:

- 30 saved near misses: 4 functions admitted 5 inferred-record candidates. All compiled; all retained
  their baseline score; **no new matches or improvements** were found in that sample.
- Two older snapshots of the large function: 4 further inferred-record candidates compiled but did
  not improve scores 950 or 210. Inferring all visible fields is not equivalent to recovering the
  original source's actual record/alias structure.
- These neutral outcomes argue for bounded trials and measured prioritisation, not a blanket promise
  that inferred structures will increase progress.

449 unclassified captured repairs were triaged locally: 66 already arose from recorded automatic
paths, 4 were duplicate evidence, and 379 remain reviewed for further investigation. Category labels
summarise source differences; they do not isolate causes. The largest source-pattern groups included
address substitutions and source-line layout changes. Mixed edits and missing failed baselines are
retained. Source/layout/flags must be replayed independently before adding another automatic rule.

## Follow-up: transcript-driven agent efficiency

A local audit of 12 recent sessions with compiles recorded 155 compiles and one match. This is an observation,
not a controlled model comparison: selected functions and histories differed. Of 16,913,071 recorded total
tokens, 16,280,168 were cache reads; do not present that total as fresh input or billed API cost.

Three machine-generated repair batches emitted 18 compile errors against unbuildable starting C. Generated
batches now preflight the baseline once and cache its diagnostic under source/build/target/claim scope.
New inputs, a new claim or a subsequent explicit compile allow another check. Explicit authored patches
remain available to fix the C. Synthetic ROM and imported-mode regressions verify one baseline invocation
and zero generated trials, rather than six repeated failures. A private replay of the actual invalid
transcript draft also produced one real compiler invocation across two requests, with zero variants.
Normal exact judging is unchanged.

The agent runner's local-family list previously omitted the new shared copy/scope/layout families. It now
uses those shared generators and profile/mismatch priorities, and trains only measured, non-cached outcomes.
This closes a tool-availability gap; no matching-rate increase has yet been measured after installation.

Compact project facts previously spent their bounded response on address-range overrides before useful
progress information. They now summarise range counts and retain progress. Repeated identical claim packets
are suppressed only after normal claim/lease handling, and a new lease or changed packet gets the full brief.

Two inspected cross-region agent matches only substituted global symbol identifiers. A normally verified
agent success can now finish a recorded donor transfer and feed the existing mapping mechanism. This requires
unambiguous donor provenance, unchanged target/donor stamps, symbol-only token edits and resolvable symbol
names. Comments alone, literal/operator/type/local-variable edits, and reopened donors cannot teach this
shortcut. Existing independent-pair requirements still apply before data mappings are reused elsewhere.

## KMC GCC 2.7.2: floating product intermediate placement

**Controlled regression of an existing captured match:** 32-bit big-endian MIPS,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`. Instead of storing the integer-to-float conversion in a temporary,
store the first multiplication there and consume it in the second multiplication. Keep the casts and
parenthesized product order explicit; floating reassociation is not assumed safe.

The bounded `float_product_intermediate` family generated three candidates. One recovered the successful
compiler control output; two improved the regression score from 180 to 35 and 55. The normal linked-byte
judge also accepted one exactly in a private ROM/project-state copy. This recovers an already-captured
match and adds **no new game progress**. Allocation depends on which same-type local holds the product;
the successful case reused a local whose earlier branch lifetime had ended.

The family excludes loops/gotos, calls, volatile access, address-escaped temporaries, extra uses in the
expression and later live uses. It is shared by draft repairs and agent local repairs in both judge modes.
An intermediate store can change rounding; every proposed match still requires the normal exact judge.

**Parser regression:** a homemade typed field macro using `(*(t*)((u8*)(p)+(o)))` compiled to identical
normalized KMC function bytes before/after expansion and yielded variants from the installed permuter.
This fixes parser coverage, not evidence of extra matched functions. Three other captured repair replays
found no control recovery under 12 trials each; offline replay reports them without applying candidates.

## Captured pointer, initialization and terminal-guard regressions

**Controlled recoveries of existing captures**, KMC GCC 2.7.2, big-endian MIPS,
`-G0 -mips3 -mgp32 -mfp32 -O2 -g3`:

- Advancing an existing dead pointer cursor for the first dereference, then copying it into a differently
  typed pointer local, recovered the captured pointer-chase match.
- Swapping two independent scalar zero initializations recovered an allocation/scheduling near miss.
  Grouped local declarations must be understood before identifying legal scalar candidates.
- Replacing a terminal negative conditional (with a dead boolean copy) with an early return using an
  existing initialized result local recovered the captured control-flow match.

**IDO 5.3 controlled recovery**, big-endian MIPS,
`-O2 -mips2 -G 0 -non_shared -Xcpluscomm -Wab,-r4300_mul`: repeating a prior member assignment after an
intervening same-object flag store recovered its captured match. A second placement compiled but did
not match. Repeating stores is a scheduling hypothesis, not a generic optimization or semantic guarantee;
volatile accesses, calls and complex expressions are excluded from these proposals.

All four generated families recovered their successful compiler controls, and separate normal linked-byte
checks in private project/ROM copies accepted one candidate per case. These are regression recoveries,
not new matched functions. Only the destination's normal judge may adopt them in future work.

**Evidence correction:** random rewrite context can flow into cascaded cross-function matches. At audit
time, 436 of 444 random-labelled captures had a different candidate hash; six of the eight remaining
captures also had a different recorded baseline. They cannot establish a direct rewrite cause. The
source-matching repeated-store capture above had the matching base; the other direct case was already
recovered by `table_slot_lifetime`. Preserve inherited origin metadata, but do not train a new rule from
the final rewrite name alone. Historical reviews remain unchanged.
