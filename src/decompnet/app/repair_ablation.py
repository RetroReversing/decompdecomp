"""Bounded edit ablation against a frozen successful output, never a game-match verdict."""
import difflib
import hashlib

from .repair_tokens import tokens


def minimize(before, after, preserves, *, trials=12, stopped=lambda:False):
    if not 1 <= trials <= 128:
        raise ValueError('Use 1–128 ablation trials.')
    left, right = tokens(before), tokens(after)
    edits = [op for op in difflib.SequenceMatcher(None,left,right,autojunk=False).get_opcodes() if op[0]!='equal']
    retained = set(range(len(edits)))
    def materialize(keep):
        pieces = []; cursor = 0
        for index,(_,a,b,c,d) in enumerate(edits):
            pieces += left[cursor:a]
            pieces += right[c:d] if index in keep else left[a:b]
            cursor = b
        return ''.join(pieces+left[cursor:])
    tested = []; seen = set(); changed = True
    while changed and len(tested)<trials and not stopped():
        changed = False
        for index in sorted(retained):
            if len(tested)>=trials or stopped():
                break
            candidate = materialize(retained-{index})
            key = hashlib.sha256(candidate.encode()).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            ok = preserves(candidate)
            tested.append({'edit':index,'preserves_output':bool(ok),'source_sha256':key})
            if ok:
                retained.remove(index);changed=True
    return {'source':materialize(retained),'edits':len(edits),'retained':sorted(retained),
            'reverted':len(edits)-len(retained),'trials':tested,
            'exhausted':len(tested)>=trials or stopped(),
            'granularity':'C tokens (including whitespace and comments)',
            'verdict':'Frozen compiler-output ablation only; not semantic necessity or a linked game-match verdict.'}
