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

Single-function deep search also explores one bounded local detour per local-repair burst under its
existing deadline/compile allowance. It returns the saved best source when the detour fails; only the
normal judge can save a later improvement. Persistent multi-function iterative state is separate.

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
