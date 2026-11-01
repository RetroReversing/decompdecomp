"""Advisory target-aligned mismatch regions for retaining useful source alternatives.

Region identity comes from the complete target instruction stream, not candidate row positions.
Inserted instructions cannot slide the comparison. This never supplies a match verdict.
"""
from .permuter_focus import normalized

REGION_INSNS = 16


def snapshot(rows):
    targets, losses = [], {}
    for row in normalized(rows):
        if len(row) != 3:
            return None
        marker, target, _ = row
        index = len(targets)
        if target:
            targets.append(target)
        else:
            index = max(0, index-1)
        if marker != ' ':
            region = index // REGION_INSNS
            losses[region] = losses.get(region, 0) + 1
    if len(targets) < REGION_INSNS:
        return None
    return {'target': targets, 'losses': {str(k): v for k, v in losses.items()}}


def gain(before, after):
    """Count recovered mismatches only with the same target stream; total score may still regress."""
    if not before or not after or before['target'] != after['target']:
        return 0
    return sum(old - after['losses'].get(region, 0) for region, old in before['losses'].items()
               if old > after['losses'].get(region, 0))


def damage(before, after):
    """Count new mismatches in previously correct target blocks; advisory, never a veto."""
    if not before or not after or before['target'] != after['target']:
        return 0
    return sum(loss for region, loss in after['losses'].items() if not before['losses'].get(region))
