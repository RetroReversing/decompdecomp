"""Allocator and scheduling clues from displayed MIPS rows, never a match verdict."""
import re
from collections import Counter

from . import permuter_focus

REGISTER = re.compile(r'\$[A-Za-z0-9]+\b')


def diagnose(rows, arch=None):
    rows = permuter_focus.normalized(rows)
    rows = [r for r in rows if isinstance(r,(list,tuple)) and len(r)==3 and all(isinstance(c,str) for c in r)]
    changed = [r for r in rows if r[0]!=' ']
    mapping, reverse = {}, {}
    coherent = bool(changed)
    for marker,left,right in changed:
        a,b = REGISTER.findall(left),REGISTER.findall(right)
        if marker != 'r' or REGISTER.sub('$reg',left) != REGISTER.sub('$reg',right) or not a or len(a)!=len(b):
            coherent = False
            continue
        for target,candidate in zip(a,b):
            if mapping.get(target,candidate)!=candidate or reverse.get(candidate,target)!=target:
                coherent = False
            mapping[target],reverse[candidate] = candidate,target
    swaps = sorted((a,b) for a,b in mapping.items() if a!=b) if coherent else []
    # Equal instruction multisets suggest movement, not permission to reorder side effects.
    left = Counter(r[1] for r in changed if r[1])
    right = Counter(r[2] for r in changed if r[2])
    scheduling = bool(changed) and left == right
    frame = None
    if arch == 'mips':
        from .stack_layout import diagnose as stack
        frame = stack(arch,rows)
    slots = sorted({(r['target'],r['candidate']) for r in (frame or {}).get('local_operands',[]) if r['delta']})
    return {'register_map':swaps, 'scheduling':scheduling,
            'target_frame':(frame or {}).get('target_frame'), 'candidate_frame':(frame or {}).get('candidate_frame'),
            'spill_offsets':slots, 'differences':len(changed)}


def signature(rows, arch=None):
    d = diagnose(rows,arch)
    return (permuter_focus.classify(rows),d['target_frame'],d['candidate_frame'],
            tuple(map(tuple,d['spill_offsets'])),tuple(map(tuple,d['register_map'])),d['scheduling'])


def text(rows, arch=None):
    d = diagnose(rows,arch)
    hints = []
    if d['register_map']:
        hints.append('consistent register substitution '+', '.join(a+'→'+b for a,b in d['register_map'][:6])+
                     '; try lifetime/split-reuse repairs before broad flag search')
    if d['spill_offsets']:
        hints.append('local stack offsets differ '+', '.join(f'{a:#x}→{b:#x}' for a,b in d['spill_offsets'][:4])+
                     '; explicit stack fields need not reproduce compiler spills')
    if d['target_frame'] is not None:
        hints.append(f"frame {d['target_frame']} target / {d['candidate_frame']} candidate")
    if d['scheduling']:
        hints.append('same displayed instruction multiset, different order; try dependency-safe initialization order')
    return 'Allocation clues (advisory): '+'; '.join(hints)+'.' if hints else ''
