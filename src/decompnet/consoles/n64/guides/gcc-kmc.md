# Matching KMC GCC 2.7.2 (many third-party N64 games)

Flags are usually `-O2 -mips3 -G0` (some games `-O1` or `-O3`; ask the user to change the project's
compiler settings if nothing matches at all).

When instruction order differs around calls or returns, use `search_flags`: it also checks `-g` and `-g3`
while keeping `-O2`. Debug line labels can prevent the old assembler moving an instruction into a delay slot.
A matching result proves the flags for that function; keep the override scoped to the verified address range.
The automatic compiler survey also checks these debug variants on saved attempts.

Traits
- Unconditional jumps inside a function are `j label`; IDO would use `b`. (That's how the app guessed.)
- `move` is written as `addu rd, rs, $zero`.
- Branch-likely (`beql`, `bnel`) is common in loops and simple ifs.
- The function's local variable declaration order affects register allocation and stack slots.
  Reorder declarations before rewriting logic.
- Expressions are evaluated mostly left to right; swapping operands of `+`, `==`, `&&` can change
  register use or instruction order.

Things to try when registers differ (`r`)
- Introduce or remove a temporary variable; reuse one variable for two values.
- Change `int` to `s16`/`u8` (or back) for locals and parameters; signedness changes comparisons and
  extension instructions.
- `for` vs `while` vs `do { } while` loops produce different block layouts. `do-while` has no
  initial check.
- Pre/post increment, `x += 1` vs `x++`, pointer walking (`*p++`) vs indexing (`p[i]`).
- Early `return` vs `if/else` with one return at the end.
- `if (a) { if (b) ... }` vs `if (a && b)`.

Calls and prototypes
- Without a prototype, GCC assumes `int f()` and promotes arguments: floats become doubles,
  `s16`/`u8` arguments are not truncated. Missing extensions or `cvt.d.s` before calls point at
  prototype problems: add the prototype (edit_context).

switch
- Dense cases become a jump table (`jtbl_` in .rodata, `jr $reg`); sparse ones become compare chains.
  Your jump table must equal the ROM's, so case values and order matter.

Measured large-function findings (`-O2 -g3`, 32-bit MIPS)
- Try a bounded small `__builtin_memcpy` when explicit loads/stores copy a short string. Preserve the
  source array's actual size/alignment and use a real destination buffer; never infer contiguous locals.
- Recover real records and packet/command fields before broad declaration shuffling. Typed memory
  accesses can change alias analysis, scheduling and registers.
- Try removing synthetic one-shot wrappers, splitting a command pointer into a small block, and sharing
  an array field address with a negative offset back to its base. Lifetime changes can need paired edits.
- Keep a bounded alternative even when total score gets worse: it may fix one region and allow the next
  edit to finish the match. Never replace saved best C with an unverified regression.
- These were observed on one 883-instruction function, not established for every GCC version. The
  repository's `docs/COMPILER_MATCHING_LEARNINGS.md` records evidence, failed ideas and update rules.
