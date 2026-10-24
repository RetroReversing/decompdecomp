# Matching IDO 5.3 / 7.1 (most first-party N64 games)

Usual flags: `-O2 -mips2` (some files `-O1`, `-g`, or `-mips1`). If almost nothing matches, the project
may need a different version (5.3 vs 7.1) or flags: tell the user, they can switch it in the app.

Traits
- Unconditional jumps inside functions are `b label` (`beq $zero, $zero`).
- Expression structure can change output: `a = b; c = a;` and `c = a = b;` are
  different expressions. Test this separately from changes to line breaks alone.
- It reuses registers in a predictable order; extra temporaries often *don't* help, while reordering
  statements or changing where a variable is first assigned does.
- Common idioms: `if (x) { } else { }` with empty blocks, `do { ... } while (0);` around a sequence,
  re-reading a global instead of keeping it in a local, `*(&x)`.

Floats
- IDO keeps float constants in .rodata (`lwc1 $f4, %lo(D_...)`) unless they're simple: writing `1.0f`
  vs `1.0` (double) matters.

Argument home slots
- IDO stores `$a0-$a3` to the caller's frame (`sw $a0, 0x18($sp)`) when their address is taken or at
  -O0/-g. Spills like that tell you a parameter is used after a call.

Static functions
- `static` functions may be called with a different convention only in the same file; for matching a
  single function it rarely matters, but keep the name the ROM uses.

Source line layout (observed; mechanism unconfirmed)
- Preserve a candidate's line breaks before trying automatic formatting or one-line storage.
  A private IDO 5.3 same-source comparison matched with preserved whitespace (score 0),
  while replacing every whitespace run with one space scored 120 and changed a call/load
  ordering. This establishes layout sensitivity in that example, not a newline-only cause.
  The broader pass added 22 matches, but header/global-declaration fixes also changed between
  passes, so those 22 cannot all be attributed to formatting. Full-image acceptance is separate.
- Source locations may influence compiler decisions, but instruction scheduling has not been
  established as the cause here. Extraction, preprocessing and macro effects must be ruled out.
- For a controlled check, keep the compiler executable, flags, headers, macros, symbol addresses
  and linker setup fixed. Save both inputs; change only newlines between ordinary C tokens.
  Do not move preprocessor directives, continue `//` comments, or change `__LINE__` uses.
  Compare preprocessed token sequences and linked instruction bytes, not whole object files
  whose debug records can differ. Repeat both builds to check determinism.
- Keep formatting variants as separate attempts and rejudge after formatting matched C.
  A per-function match must still pass the complete image/cartridge rebuild. Never change the
  target bytes or judge to accommodate a formatting variant.
