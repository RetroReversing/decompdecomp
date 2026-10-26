"""Conservative global addresses from raw MIPS instruction words in annotated assembly."""

import re


def addresses(asm):
    """Read LUI plus signed memory offsets from raw MIPS words, never pretty relocation labels."""
    high, result = {}, set()
    for match in re.finditer(r'/\*\s*[0-9A-Fa-f]+\s+[0-9A-Fa-f]+\s+([0-9A-Fa-f]{8})\s*\*/', asm):
        word = int(match[1], 16)
        op, rs, rt, imm = word >> 26, (word >> 21) & 31, (word >> 16) & 31, word & 65535
        if op == 15:
            high[rt] = imm << 16
            continue
        if op in {9, 32, 33, 35, 36, 37, 40, 41, 43, 49, 53, 57, 61} and rs in high:
            result.add((high[rs] + (imm if imm < 32768 else imm - 65536)) & 0xffffffff)
        if op in {4, 5, 6, 7, 1, 2, 3} or op == 0 and word & 63 in {8, 9}:
            high.clear()
        elif op in {9, 32, 33, 35, 36, 37}:
            high.pop(rt, None)
        elif op == 0 and word:
            high.pop((word >> 11) & 31, None)
        elif op not in {0, 40, 41, 43, 49, 53, 57, 61}:
            high.clear()
    return result

