"""Read a uniform MIPS frame-size difference from scored instruction pairs; no match verdicts."""
import re


def frame_growth(rows):
    """A small positive frame delta with unchanged saved-register instructions, or no evidence."""
    if not rows:
        return None
    changed = [row for row in rows if row[0] != ' ']
    if len(changed) < 4 or any(row[0] != 's' for row in changed):
        return None
    frame = re.compile(r'addiu\s+\$sp,\s*\$sp,\s*(-?0x[0-9a-fA-F]+|-?\d+)$')
    ends = []
    for _, target, candidate in changed:
        a, b = frame.fullmatch(target), frame.fullmatch(candidate)
        if a and b:
            ends.append((int(a[1], 0), int(b[1], 0)))
    if len(ends) != 2 or ends[0][0] >= 0 or ends[0][1] >= 0 or ends[1] != (-ends[0][0], -ends[0][1]):
        return None
    delta = abs(ends[0][0])-abs(ends[0][1])
    if delta not in {8, 16, 24, 32}:
        return None
    # Saved-register loads/stores must all show the same shift, without changing registers/opcodes.
    slot = re.compile(r'((?:sw|lw|sdc1|ldc1)\s+\$\w+,\s*)(0x[0-9a-fA-F]+|\d+)\(\$sp\)$')
    for _, target, candidate in changed:
        if frame.fullmatch(target) and frame.fullmatch(candidate):
            continue
        a, b = slot.fullmatch(target), slot.fullmatch(candidate)
        if not a or not b or a[1] != b[1] or int(a[2], 0)-int(b[2], 0) != delta:
            return None
    return delta
