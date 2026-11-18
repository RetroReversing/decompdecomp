"""Ghidra's draft of an Xbox 360 function as C its own PPC compiler accepts.

Xbox 360 has no m2c: it has no 64-bit PowerPC target (``Xbox360.m2c == ""``), so autopilot's machine drafts
come from Ghidra's decompiler instead, the way the PC console's do. ``forks/ghidra.py`` decompiles the
delinked ELF64 object objdiff judges against and returns C per function; this module turns one of those
into (prelude, definition) for the function's source file.

Ghidra drafts each function of a delinked object without knowing what is outside it, so the C it writes is
not C yet: its types (``undefined4``, ``undefined8``) need declarations, and PPC64's 64-bit ``long`` must
not silently become MSVC's 32-bit one. What it cannot stand for at all -- a register read (``in_LR``), a
bad instruction (``halt_baddata``) -- makes the draft None rather than wrong C.
"""

from __future__ import annotations

import re

GHIDRA_HEADER = "ghidra.h"

# Ghidra's types, portable between the real XDK's cl.exe (_MSC_VER, LLP64) and the free xsdk GCC (LP64).
# A PPC64 `long` is 8 bytes and `int` 4, which is what `longlong`/`undefined4` say here: on cl.exe a bare
# `long` would be 4 bytes and quietly change the code the draft describes.
GHIDRA_H = """\
/* Ghidra's decompiler types, for drafts of 64-bit PowerPC code. Written by decompdecomp. */
#ifndef GHIDRA_H
#define GHIDRA_H

typedef unsigned char undefined;
typedef unsigned char undefined1, byte, uchar;
typedef signed char sbyte;
typedef unsigned short undefined2, ushort, word;
typedef short sword;
typedef unsigned int undefined4, uint, dword, uint3;
typedef int sdword;
typedef unsigned long long qword, ulonglong;
typedef long long sqword;
#ifdef _MSC_VER
typedef unsigned __int64 undefined8, uint8;
typedef __int64 longlong, int8;
#else
typedef unsigned long long undefined8, uint8;
typedef long long longlong, int8;
#endif
typedef unsigned short ushort3;
typedef unsigned char uchar3;
typedef double float10;
typedef long double longdouble;
typedef int code(void);

#define CONCAT22(a, b) ((uint)((((uint)(a)) << 16) | ((uint)(b) & 0xFFFF)))
#define SUB42(a, b) ((ushort)((uint)(a) >> 16))
#define SUB41(a, b) ((uchar)((uint)(a) >> 24))

#endif
"""

# What no C stands for: a register the draft reads as an input, a stack offset Ghidra could not place, a
# bad instruction, or a half of a register assembled from bytes Ghidra lost track of. Also the x86-only
# artifacts that would mean the draft came from somewhere else.
_UNWRITABLE = re.compile(
    r"\b(in_[A-Z][A-Z0-9]*|unaff_\w+|extraout_\w+|in_stack_\w+|stack0x\w+|halt_baddata|swi|"
    r"CONCAT\d+|SUB\d+|ZEXT\d+|SEXT\d+|POPCOUNT)\b|\.\_\d+_\d+_|WARNING: Bad instruction")

_HEAD = re.compile(r"^(?P<ret>[^\n;{}]*?)\s*(?P<name>FUN_[0-9A-Fa-f]{8,16})\s*\((?P<params>[^()]*)\)\s*\{", re.M)
_GLOBAL = re.compile(r"\b_*(?:DAT|BSS)_([0-9A-Fa-f]{8,16})\b")
_CALLEE = re.compile(r"\bfunc_(0x[0-9A-Fa-f]+)\b")


def c_name(symbol: str) -> str | None:
    """The function's name as the project's functions.json spells it (FUN_92179040), or None.

    The address keeps the case it was given in: the delinked target's symbol is exactly what objdiff pairs
    the compiled object against, so a draft must define that spelling and not a normalised one.
    """
    found = re.search(r"(?:FUN|func)_([0-9A-Fa-f]{8,16})", symbol or "")
    return f"FUN_{found.group(1)}" if found else None


def _widths(text: str) -> str:
    """PPC64's 8-byte `long`/`ulong` as the 8-byte types a PPC compiler really has, whatever its data model."""
    text = re.sub(r"\bunsigned long\b", "ulonglong", text)
    text = re.sub(r"\bsigned long\b", "longlong", text)
    text = re.sub(r"(?<!long )\blong\b(?! long)", "longlong", text)
    text = re.sub(r"\bulong\b", "ulonglong", text)
    return text


def _calls_site(symbols: dict[str, str], body: str) -> set[str]:
    """The callees a draft names that the project can spell (its own functions, by name)."""
    return {match for match in _CALLEE.findall(body) if f"FUN_{match[2:].upper()}" in symbols}


def compilable(draft: str, symbol: str, symbols: dict[str, str] | None = None,
               known: str = "") -> tuple[str, str] | None:
    """Ghidra's draft as C that compiles in the function's source file, (prelude, definition), or None.

    ``symbol`` is the function's name (FUN_9218EA98, as the project spells it); ``symbols`` maps each
    function's name to what the object calls it, so a callee the project knows keeps its real name;
    ``known`` is the headers' and the file's text, whose declarations are used as they already are.
    """
    symbols = symbols or {}
    text = re.sub(r"/\*.*?\*/", "", draft, flags=re.S)
    if _UNWRITABLE.search(text):
        return None
    name = c_name(symbol) or next(iter(re.findall(r"\bFUN_[0-9A-Fa-f]{8,16}\b", text)), None)
    head = _HEAD.search(text)
    if name is None or head is None:
        return None
    text = _widths(text)
    head = _HEAD.search(text)
    signature = f"{head.group('ret').strip()} {name}({head.group('params').strip()})".strip()
    body = text[head.end() - 1:].rstrip()  # head ends on the opening brace: keep it with the body
    if not body.endswith("}"):
        return None

    # Symbols the project already names keep that name: a callee is FUN_<address> rather than Ghidra's
    # func_0x<offset>, which names nothing outside the delinked object it came from.
    for offset in _CALLEE.findall(body):
        callee = f"FUN_{offset[2:].upper()}"
        if callee in symbols:
            body = re.sub(rf"\bfunc_{offset}\b", symbols[callee], body)

    prelude = [f'#include "{GHIDRA_HEADER}"']
    for address in sorted({m for m in _GLOBAL.findall(body)}):
        spelling = next(m for m in re.findall(rf"_*(?:DAT|BSS)_{address}\b", body))
        if not re.search(rf"\b{spelling}\b", known):
            prelude.append(f"extern undefined4 {spelling};")
    return "\n".join(prelude) + "\n", f"{signature}\n{body}"
