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

/* Ghidra calls a 1-byte truth value `bool`; C has none without <stdbool.h>, which these compilers don't ship,
   and the compiler then reports the *function name* as a syntax error. stdbool defines `bool` as a macro, so
   this stands aside when it was included. */
#if !defined(__cplusplus) && !defined(bool)
typedef unsigned char bool;
#endif
#if !defined(__cplusplus) && !defined(true)
enum { false = 0, true = 1 };
#endif

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

_HEAD = re.compile(r"^(?P<ret>[^\n;{}]*?)\s*(?P<name>(?:FUN|func)_[0-9A-Fa-f]{8,16})\s*\((?P<params>[^()]*)\)\s*\{", re.M)
# The project's own prototype for a function, as its unit file declares it: the draft must agree with this,
# because the file keeps that declaration and C rejects a definition whose type differs (C2371).
_DECL = re.compile(r"^[ \t]*(?P<ret>[A-Za-z_][\w \t*]*?)\s+(?P<name>(?:FUN|func)_[0-9A-Fa-f]{8,16})\s*\((?P<params>[^;{)]*)\)\s*;", re.M)
_GLOBAL = re.compile(r"\b_*(?:DAT|BSS)_([0-9A-Fa-f]{8,16})\b")
_CALLEE = re.compile(r"\bfunc_(0x[0-9A-Fa-f]+)\b")
_VALUE_RETURN = re.compile(r"\breturn\s+([^;]+);")


def _params(text: str) -> list[tuple[str, str]]:
    """(type, name) per parameter; `void *` has no name and `void` alone means none."""
    found = []
    for part in text.split(","):
        part = part.strip()
        if not part or part == "void":
            continue
        match = re.match(r"^(.*?)\s*([A-Za-z_]\w*)$", part)
        if match and match.group(1).strip():
            found.append((match.group(1).strip(), match.group(2)))
        else:
            found.append((part, ""))  # one token is a type with no name (the project's own prototypes)
    return found


def _same_type(left: str, right: str) -> bool:
    """Whether two written types mean the same thing for C (pointer spacing varies in Ghidra's output)."""
    return " ".join(left.replace("*", " * ").split()) == " ".join(right.replace("*", " * ").split())



def c_name(symbol: str) -> str | None:
    """The function's name as the project's functions.json spells it (FUN_92179040), or None.

    The address keeps the case it was given in: the delinked target's symbol is exactly what objdiff pairs
    the compiled object against, so a draft must define that spelling and not a normalised one.
    """
    found = re.search(r"(FUN|func)_([0-9A-Fa-f]{8,16})", symbol or "")
    # The prefix is the project's too: a map entry without a name is func_<address> (scratch.create), and turning
    # that into FUN_ defined a symbol objdiff never pairs, so autopilot's drafts never applied to those functions.
    return f"{found.group(1)}_{found.group(2)}" if found else None


def _known_callee(symbols: dict[str, str], offset: str) -> str | None:
    """The project's name for Ghidra's func_0x<address> callee, in either spelling the project uses (FUN_/func_)."""
    address = offset[2:].upper()
    return next((symbols[name] for name in (f"FUN_{address}", f"func_{address}") if name in symbols), None)


def _widths(text: str) -> str:
    """PPC64's 8-byte `long`/`ulong` as the 8-byte types a PPC compiler really has, whatever its data model."""
    text = re.sub(r"\bunsigned long\b", "ulonglong", text)
    text = re.sub(r"\bsigned long\b", "longlong", text)
    text = re.sub(r"(?<!long )\blong\b(?! long)", "longlong", text)
    text = re.sub(r"\bulong\b", "ulonglong", text)
    return text


def _calls_site(symbols: dict[str, str], body: str) -> set[str]:
    """The callees a draft names that the project can spell (its own functions, by name)."""
    return {match for match in _CALLEE.findall(body) if _known_callee(symbols, match) is not None}


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

    # The unit file already declares this function (for its callers), so the draft has to fit that
    # declaration rather than add a second, conflicting one: Ghidra's `undefined8` next to the project's
    # `void` is C2371 and the draft never builds at all. Adopt the declared types, and where a parameter's
    # type differs keep the draft's name through a local copy so the body is untouched.
    declared = next((d for d in _DECL.finditer(known) if d.group("name") == name), None)
    if declared is not None:
        wanted, written = _params(head.group("params")), _params(declared.group("params"))
        if len(wanted) != len(written):
            return None  # a different arity is not this function's draft
        adopted, copies = [], []
        for index, ((draft_type, draft_name), (decl_type, _)) in enumerate(zip(wanted, written), 1):
            if _same_type(draft_type, decl_type):
                adopted.append(f"{decl_type} {draft_name}")
            else:
                adopted.append(f"{decl_type} arg{index}")
                copies.append(f"    {draft_type} {draft_name} = ({draft_type})arg{index};")
        signature = f"{declared.group('ret').strip()} {name}({', '.join(adopted) or 'void'})"
        if copies:
            body = body.replace("{", "{\n" + "\n".join(copies), 1)
        if _same_type(declared.group("ret"), "void") and not _same_type(head.group("ret"), "void"):
            # A void function cannot return a value; what it returned is usually Ghidra's `return 0`.
            body = _VALUE_RETURN.sub(
                lambda m: "return;" if m.group(1).strip() in ("0", "(undefined4)0", "(undefined8)0")
                else f"{m.group(1).strip()};\n    return;", body)

    # Symbols the project already names keep that name: a callee is FUN_<address> rather than Ghidra's
    # func_0x<offset>, which names nothing outside the delinked object it came from.
    for offset in _CALLEE.findall(body):
        if (callee := _known_callee(symbols, offset)) is not None:
            body = re.sub(rf"\bfunc_{offset}\b", callee, body)

    prelude = [f'#include "{GHIDRA_HEADER}"']
    for address in sorted({m for m in _GLOBAL.findall(body)}):
        spelling = next(m for m in re.findall(rf"_*(?:DAT|BSS)_{address}\b", body))
        if not re.search(rf"\b{spelling}\b", known):
            prelude.append(f"extern undefined4 {spelling};")
    return "\n".join(prelude) + "\n", f"{signature}\n{body}"
