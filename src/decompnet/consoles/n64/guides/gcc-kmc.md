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

Register-only finishes
- A masked or narrowed value may need an existing local, rather than a new temporary or an in-place
  assignment to its input. Try `scratch = (u16)width; centre = (limit - scratch) / 2;` only when the old
  `scratch` value is dead on every path. Preserve resets before later uses, including loop back edges.
- `run_local_repairs` offers `masked_value_reuse` and `cfg_integer_lifetimes`; autopilot's repair phase
  and deep search use the same rules. Draft lifetime hints keep m2c's original names and suggest possible
  groupings. They are advice, not proof of the original declarations or a match.
- Check actual register/opcode differences before searching constants. A numeric load offset and
  `%lo(symbol)` can render differently while encoding the same operand. Search guidance normalizes
  resolvable operands; the displayed diff, score and compiler/link judge stay unchanged.
- Keep an instruction-order alternative alongside a register-only alternative. Numeric similarity
  alone can discard a source shape which a later small edit can finish.

Expression and frame shape
- Address association can change allocation/order: compare `(base + displacement) + index` with
  `base + (displacement + index)` while preserving pointer bounds, types and evaluation side effects.
- An optimized-away conditional or comma-expression placement can still change this old compiler's
  intermediate lifetimes and final scheduling. Treat this as a measured source-shape experiment,
  not a reason to invent arbitrary predicates or read uninitialized values.
- An inline helper's aggregate/bitfield local affected stack-frame reservation in one verified case.
  This is an observation, not a general padding rule. Inspect all stack offsets and verify the bytes.
- Keep the successful source, header and compiler profile together. Do not switch compiler hosts or
  repeat wide flag sweeps once controlled comparisons show identical output for the relevant input.

See `docs/COMPILER_MATCHING_LEARNINGS.md` for controlled evidence and failed experiments.

### Address association and call initialization

Equivalent byte-pointer expressions can schedule differently in KMC GCC. The bounded
`address_association` family tries constant displacement grouping, including candidate-owned
inline helpers. Calls and incrementing expressions are excluded from these transformations.
The `call_initialization` family can move a constant local reset into a call's first argument
when scalar CFG analysis proves every relevant path reaches that call before a read or
redefinition. Unknown syntax, escapes and unresolved loops are rejected.

Stack diagnostics distinguish frame size, register-save offsets and other stack operands.
Do not infer an object's size or add padding from the frame delta alone. The
`experimental_bitfield_frame` local family is available only by explicit selection; it is
excluded from automatic repair defaults. Its compiler-specific signed-halfword helper storage
is an experiment, and every candidate still requires the ordinary exact judge.

#### Inline boundaries and repeated bounds tests

With KMC GCC 2.7.2 `-O2 -g3`, an inline scalar helper can change front-end expression
shape, register allocation and scheduling even when it emits no call. Repeated axis tests may
need the same helper form together: one controlled six-comparison regression recovered an exact
linked match with the grouped form, while single-site changes did not. Use the local
`inline_scalar_shape` family before rewriting each site manually. `combined_scheduling` offers
bounded bridges with temporary, stack-address or loop shapes; the normal judge must verify them.
This is compiler-specific evidence, not a promise that helpers improve every function.

### Near matches with calls and pointer fields

Use `target_call_argument` when a candidate-owned no-argument prototype conflicts with an
explicit target delay-slot argument load. `known_address_alias` tries auto-address aliases
through one byte-pointer base. Single-field records, their raw-access alternatives and direct
boolean masks can change scheduling. Try both splitting a call-result temporary and reusing
a compatible dead result slot; neither is consistently best. The bounded local families also
combine these changes and can stage field loads in target-observed offset order. Every trial
uses the ordinary judge; do not change shared prototypes or accept a lower score as a match.
