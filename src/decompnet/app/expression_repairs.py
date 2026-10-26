"""Small source hypotheses isolated from captured repairs; only the exact judge may keep them."""

import re

from ..forks.csource import _blank, find

IDENT = r'[A-Za-z_]\w*'
LIMIT = 6


def variants(source: str, name: str, arch: str) -> list[str]:
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source)
    opening = clean.find('{', span[0], span[1])
    if opening < 0 or '#' in clean[opening:span[1]]:
        return []
    body = clean[opening:span[1]]
    result = []

    def edit(start, end, replacement):
        candidate = source[:start] + replacement + source[end:]
        if candidate not in result:
            result.append(candidate)

    # A narrowing assignment can produce different scheduling from an explicit mask. These are hypotheses,
    # not claims of C equivalence (promotion/signedness can differ). Leave nested/side-effecting expressions out.
    mask = re.compile(rf'\(\s*(?P<x>{IDENT})\s*\+\s*(?P<n>0[xX][0-9a-fA-F]+|[0-9]+)\s*\)'
                      r'\s*&\s*(?P<mask>0[xX](?:[fF]{4}|[fF]{2}))(?![\w.])')
    for m in mask.finditer(body):
        typ = 'unsigned short' if len(m['mask']) == 6 else 'unsigned char'
        edit(opening + m.start(), opening + m.end(), f'({typ})({m["x"]} + {m["n"]})')
        if len(result) >= 2:
            break

    if arch in {'mips', 'ppc', 'x86', 'arm', 'thumb'}:
        # A symbol's address is different from reading its value. Only explicit &symbol or a declared
        # array decay qualifies. Never convert a scalar/pointer global load to its label's numeric address.
        arrays = set(re.findall(r'\bextern\s+(?:volatile\s+|const\s+)?(?:unsigned\s+)?\w+\s+'
                                r'(D_[0-9A-Fa-f]{8})\s*\[\s*\]\s*;', clean[:opening]))
        address = re.compile(r'\(\s*(?:u32|s32|unsigned\s+int|int)\s*\)\s*'
                             r'(?P<wrap>\()?\s*(?P<amp>&\s*)?(?P<sym>D_[0-9A-Fa-f]{8})(?!\w)\s*(?(wrap)\))')
        for m in address.finditer(body):
            if m['amp'] or m['sym'] in arrays:
                edit(opening + m.start(), opening + m.end(), '0x' + m['sym'][2:])
                if len(result) >= 4:
                    break

    # Cache a pointer-valued global at the start of a local block, before an intervening store can alias
    # it. This intentionally explores load order; exact verification, not a heuristic, decides correctness.
    globals_ = list(re.finditer(r'\bextern\s+(?P<type>(?:void|char|u8|s8|s32|u32)\s*\*)\s*'
                               rf'(?P<name>{IDENT})\s*;', clean[:opening]))
    for declaration in globals_:
        global_name = declaration['name']
        call = re.compile(rf'\b{IDENT}\s*\(\s*(?P<arg>{re.escape(global_name)})\s*,')
        for m in call.finditer(body):
            stack = []
            for pos, char in enumerate(body[:m.start()]):
                if char == '{':
                    stack.append(pos)
                elif char == '}' and stack:
                    stack.pop()
            if not stack or '=' not in body[stack[-1] + 1:m.start()]:
                continue
            variable = '__repair_cached_pointer'
            if re.search(r'\b' + variable + r'\b', clean):
                continue
            at = opening + stack[-1] + 1
            candidate = source[:opening + m.start('arg')] + variable + source[opening + m.end('arg'):]
            candidate = candidate[:at] + f'\n{declaration["type"]}{variable} = {global_name};\n' + candidate[at:]
            if candidate not in result:
                result.append(candidate)
            break
        if len(result) >= LIMIT:
            break
    return result[:LIMIT]
