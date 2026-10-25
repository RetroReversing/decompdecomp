# Matching PlayStation (PS1) code

CPU: the R3000A, MIPS I, little-endian. Things that look odd at first:
- Load delay slot: the instruction after a load can't use the loaded value, so you'll see `nop`s or reordering
  right after loads. Branch delay slots exist too; there's no branch-likely, no 64-bit registers, no FPU (a
  `float` becomes calls to `__addsf3`, `__mulsf3`...).
- `mult`/`div` put their results in HI/LO (`mflo`, `mfhi`). A division is followed by checks for divide by zero
  (`break 7`) and overflow (`break 6`): Sony's assembler added them, and the project's build reproduces that.
  Write a plain `/` or `%`.
- `$gp`-relative small data (-G8, sometimes -G4): small globals are read with one instruction,
  `lw $v0, %gp_rel(sym)($gp)`, instead of `lui` + `lw`. Declare such globals in context.h *without* `extern`
  (`s32 D_8001B278;`): the compiler then treats them as small data. With `extern` it builds the full address and
  the function won't match. Arrays and structs bigger than the limit are never `$gp`-relative.

Compilers: PSY-Q-era GCC 2.5.7, 2.6.0, 2.6.3, 2.7.2 (PSX and Cygnus CDK variants), 2.8.0, 2.8.1,
EGCS 2.91.66 and GCC 2.95.2, with Sony's assembler ASPSX. The supported versions depend on the host; check
Compilers & tools. PSY-Q 4.3, 4.4, 4.5 and 4.6 correspond to different GCC/EGCS versions, not one late compiler. The project builds each function the way the community's PS1 decomps do: GCC, then maspsx (which does what
ASPSX did: the `nop`s it put after loads, its division checks, how it expanded `li`), then GNU as. Versions differ in
register allocation and scheduling: if right-looking C never matches, try_compiler(function, "gcc-2.6.3-psx") or
"gcc-2.8.1-psx" tests another without recording anything, and suggest(...) the one that matches.

Libraries: PSY-Q's libgte, libgpu, libgs, libspu, libcd, libetc, libapi and libc were linked as prebuilt objects
with the game. Their functions are worth naming (rename_symbol) from what they do, and worth matching only when the
game's own code calls them a lot.

GTE (coprocessor 2, the geometry engine): `mtc2`/`mfc2`/`ctc2`/`cfc2`, `lwc2`/`swc2` and commands like `RTPS` or
`NCLIP` come from PSY-Q's GTE macros (`gte_ldv0`, `gte_rtps`...), inline assembly: a function made of them is set
aside as assembly. So is a BIOS call stub (`li t2, 0xB0; jr t2; li t1, n`).

Typical patterns: `jtbl_` jump tables in .rodata for dense switches; overlays loaded at the same address (each a
segment of its own); `D_800xxxxx` data and `func_800xxxxx` names for what nobody named yet.

## Fixed GTE intrinsics

GCC 2.6.3, 2.7.2 and 2.8.1 PS1 builds supply a small trusted GTE prelude. Use named C calls such as
`gte_ldv0(p)`, `gte_rtps()` and `gte_stsxy(out)`: submitted inline assembly and arbitrary instruction words
remain forbidden. The supported subset includes the rotation/translation matrix setters, selected vector
and matrix-column transfers, screen/depth/flag outputs, RTPS/RTPT, rotation, clipping, depth averaging and
square commands. Unsupported operations still need an implementation; their presence does not prove that
the original function was handwritten assembly. Check the available definitions in `consoles/ps1/gte.py`.
The `_b` command forms omit the two leading wait nops; `_wait` supplies one. These retain period SDK scheduling
conventions, and each C candidate must still match every linked byte.

Old SDK reference objects may contain DMPSX command placeholders instead of final GTE instructions. Expand
only recognised named operations when preparing local reference objects; never count a masked object shape
as a C match. Whole-source-file context is useful for declarations and identity, but extracted C must pass
the same judge with the actual function's compiler settings and original overlay data.
