---
title: text layout loop shapes
kind: function
compiler: KMC GCC 2.7.2 -O2 -G0 -mips3 (N64)
evidence: controlled
date: 2026-11-23
---

What the function looked like and the exact compiler and flags: a 207-instruction text-layout function (a sentence
splitter with a width limit, then a pass that makes one object per line and scales every object of a table), KMC GCC
2.7.2 `-O2 -G0 -mips3 -mgp32 -mfp32`. The saved best was m2c's output with earlier edits: a `do`/`while` over the text
under an `if (buf[0] != 0)` with an identical-arm guard before it, a loop-carried character copied through a temporary,
a pointer local for a global's address declared at the top of the block, a pointer walk plus a union counter, and the
scale written as one expression. Saved score 415 (19 register rows, 4 reordered, 2 missing).

The source change, and the measured result before and after (isolated judge, which gives the app's score; each step was
also submitted to the app): five edits, each now a decomp-repair family, chained greedily from the saved source by
`prove_chain.py` (415 -> 170 -> 85 -> 60 -> 40 -> 35 in a different order than found by hand):
- `float_chain_stage`: `v = ((a / n) * 30.0f) - 15.0f;` written one operation per statement (415 -> 390).
- `loop_entry_reload`: `v = *p; do { ...; v = *p; } while (v != 0);` under `if (g[0] != 0)` written as
  `while (*p != 0) { v = *p; ... }`, the entry load stays (390 -> 365); then the loop's constant `n = 0` (before the loop
  and at its end) moved to the top of the body, which reorg fills into the loop-back branch's delay slot (95 -> 35).
- `loop_carried_temp_copy`: `t = *p; ...; if (t == 0) {} else { v = t; continue; }` written `v = *p; ...; if (v != 0)
  { continue; }` (365 -> 345): the variable no longer takes the temporary's `$v0` by copy preference.
- `address_local_scope_sink`: `entries = &sym` declared in the block that uses it (345 -> 340): cse orders the operands of
  the commutative add by the quantity numbers, and the address register must be created after the index.
- `pointer_walk_index`: `n = 0; p = &table; do { x = *p; n += 1; ...; p = p + 1; } while (n < count);` written
  `x = ((void **) &table)[n]; ...; n += 1;` (340 -> 95): the compiler's own reduced pointer appears after the guard and
  the count's copy (`move $a1,$a0` before `lui $a0`) with it.
The function stops at 35: the target's loop-carried character copy is in `$a1` and a 0/1 flag in `$a2`, ours the other
way round (see finding/allocation-flag-avoids-preferred-register).

Failed experiments that would otherwise be repeated: types of the flag and the character (`u8`, `s8`, `s16`, `register`),
statement order of the flag set (top of body, before the test), `do { } while (0)` ladders on `current`'s statements
(each adds one reference; 16 are needed), identity inline helpers around the copy, random declaration order, the
other families' children on the 35 source (`prove_chain --children 900 --combine 10`).

What is controlled evidence and what is one function: each family's gain is measured on this one function; the chain
from the saved source reproduces 415 -> 35 with the families alone. The reasons given in the families' docstrings are
hypotheses except where the finding entry cites global.c.
