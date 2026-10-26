# SN64 compiler integration

The N64 compiler picker now includes `sn-n64-281` and `sn-n64-272-0001` alongside KMC and IDO.
They can be selected for a project or tested per function through `try_compiler` and `search_flags`.
Existing compiler choices are preserved. A compiler-map suggestion still needs matching evidence.

## Original tools, ordinary judge

The pipeline is cppn64 → cc1n64 → asn64 → PCSX-Redux PsyQ converter → big-endian ELF.
The normal linker places that ELF at the target address and compares bytes. The adapter does not
substitute guessed instruction sequences or accept a disassembly similarity score as a match.

- **2.8.1** bundles its own preprocessor and ASN64 2.78 from the pinned SN64 release.
- **2.7.2 build 0001** uses that release's preprocessor and ASN64 2.56.
- Mixing the newer compiler with the older assembler fails on `%hi`/`%lo` expressions.
- The preserved Windows 2.8.1 binary is a specific build; it is not claimed to reproduce every
  other SN64 2.8.1 build, including reconstructed Linux compilers.

Tool archives and all converter source dependencies have SHA-256 pins. The proprietary programs
run through wibo on macOS/Linux and the WSL path on Windows. The free converter is built once
with a C++20 compiler and zlib headers. Windows/WSL compilation is covered structurally by the
shared staging path; the real end-to-end validation for this addition was on Apple Silicon macOS.

The converter's source and dependency licenses stay in its installation directory. No original game
code, data or compiler executable is added to this repository.

## Setup

Accept the relevant compiler's license in **Compilers & tools** and install it. Install the
free PsyQ converter too. On macOS, install Xcode Command Line Tools first; Linux/WSL needs
`g++` with C++20 support and `zlib1g-dev`. An older 2.8.1 compiler-only pack needs reinstalling
to obtain its preprocessor and assembler. Missing tools produce a reinstall instruction rather
than downloading proprietary binaries from the matching loop.

## Bounded local comparison

A private copy of a European N64 project supplied 32 functions, 12–240 instructions long,
spread across its address ranges: 16 already matched controls and 16 unfinished candidates.
Every compiler received the same existing C and context. These sources had been developed under
KMC, so this is a compiler probe, not an unbiased model or compiler benchmark.

| Profile at its default optimisation | Compiled | Matched controls | New matches among unfinished candidates |
| --- | ---: | ---: | ---: |
| KMC GCC 2.7.2 | 32/32 | 16/16 | 0/16 |
| SN64 GCC 2.8.1 + ASN64 2.78 | 32/32 | 14/16 | 2/16 |
| SN64 GCC 2.7.2 build 0001 + ASN64 2.56 | 32/32 | 3/16 | 0/16 |

Both new matches also failed KMC at `-O0`, `-O1`, `-O2` and `-O3`, and passed the ordinary
attempt judge under 2.8.1. This is useful evidence for those functions, not proof of the compiler
for the whole game. Small functions can match more than one compiler; SDK code may differ from
engine code. Live project settings and sources were not modified.

The two new matches were committed to the private database using single-function compiler ranges,
and both were included as C in an exported mixed rebuild. That rebuilt the original analysis image
byte for byte after restoring the previously documented startup BSS addresses **in the scratch
export only**. This validates the SN adapter/export path; it does not claim to fix the existing
project's incomplete function discovery or automatic BSS detection. The copied project also
contained a non-VR4300 `pref` decoding: export now preserves such annotated words verbatim,
with a regression test, rather than asking the assembler to accept an out-of-ISA mnemonic.

Validation ran the normal `try_compiler` API, source preprocessing, integer arithmetic, external
calls/data relocations, floating-point constants, the pinned native converter build, GCC flag
search/permuter-family selection, read-only driver staging for WSL, and the exported rebuild.
The application was not reinstalled and no live compiler ranges were applied.
