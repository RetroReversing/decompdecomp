---
title: sdk narrow return opaque call
kind: finding
compiler: gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2
evidence: verified
date: 2026-11-28
---

A 546-instruction N64 audio event handler calls a library volume helper several times. Its callee's matched C
declares `s32`, while the complete relocation-free bytes match the known SDK routine and the SDK declares `s16`.
Both function definitions compile to the same bytes, so the matched C alone does not establish the interface.

With `gcc-2.7.2-kmc -G0 -mips3 -mgp32 -mfp32 -O2`, the local linked judge scored the candidate 260 before and 229
after replacing the helper declaration's return type with `s16` **and** removing ten nested one-shot scopes around an
unrelated allocation call. The live project's saved score also became 229. The two changes were tested together, so
this is not evidence for the isolated effect of either one.

Removing two stable, unescaped copies of the voice pointer then removed a redundant spill and reduced the score from
229 to 0. Both the app's linked judge and the local linked judge verified the resulting bytes. A generated child of
`opaque_pointer_copy_fold` reproduced the 229-to-0 change; the repair conservatively declines escaped or reassigned
locals and cases where the copy does not dominate its uses.
The matched source retains one one-shot scope around the original voice-address assignment.

The existing matched-prototype repair rejected the declaration's two `void *` arguments, even though those arguments
were identical in the declaration and the selected callee definition. It also relied on the matched definition's
wrong return type; identifying the whole SDK body is stronger evidence here.

This is one verified match, not a general allocator rule. The pointer-argument and copy-fold repairs have separate
synthetic regression tests; the game-specific source and ROM remain outside the repository.
