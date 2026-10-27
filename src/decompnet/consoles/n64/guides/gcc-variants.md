# Additional N64 GNU compilers

`gcc-2.8.1-pm` is the PM matching GCC fork with its paired assembler.
`gcc-egcs-2.91.66` is EGCS 1.1.2 with its paired binutils. Neither is an alias
for KMC GCC or SN64, even when version numbers overlap.

Use `try_compiler` with existing C and context before changing settings. Test
calls, data accesses and floating point as well as arithmetic. Only a linked
byte match confirms a candidate; one trivial match does not identify a whole game.
Both profiles use the shared GCC flag search, drafts and permuter weights.
EGCS defaults to `-mno-abicalls -fno-pic` for ordinary N64 executable code.

macOS uses the pinned Intel releases (Rosetta on Apple Silicon); Linux has
native x86_64 packs. Windows uses the Linux packs through WSL. Their normal
compile and byte-match paths have been checked on macOS, not physical Linux
or Windows hosts. Imported projects retain their own build configuration.

`gcc-egcs-1.1.2-4` is the original Linux EGCS release, distinct from the
reconstructed `gcc-egcs-2.91.66` profile. It needs the pinned i386 runtime.
`gcc-4.4.0-n64` is an experimental later MIPS64 ELF compiler configured for
the 32-bit big-endian N64 ABI, with pinned host GMP/MPFR libraries. Retail-era
use is unconfirmed, so it is available explicitly but excluded from routine
installed-profile comparisons. These two packs run on Linux, Windows via WSL, or macOS through the optional
local Linux VM. They have no native macOS binaries. Do not substitute a different host build
and assume it has identical target code generation.
