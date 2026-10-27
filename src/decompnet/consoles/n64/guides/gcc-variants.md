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
