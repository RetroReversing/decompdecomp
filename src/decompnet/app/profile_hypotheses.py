"""Rank profile hypotheses from target evidence, never from a familiar function name alone."""
import json

from ..arch.mips_runtime import helper


def prioritized(proj, fn, own):
    if dict(proj).get('arch')!='mips' or not own.get('toolchain','').startswith('ido-'):
        return []
    flags=list(own['flags'])
    out=[]
    if helper(dict(fn).get('asm','')):
        # Target O32 return/home-slot evidence is required before changing ISA/ABI hypotheses.
        kept=[];i=0
        while i<len(flags):
            flag=flags[i]
            if flag=='-mabi':
                i+=2;continue
            if not (flag.startswith(('-O','-mips','-mabi=','-g')) or flag in {'-32','-n32','-64','-Wab,-r4300_mul'}):
                kept.append(flag)
            i+=1
        out.append(['-O1','-mips3','-32',*kept])
    meta=json.loads(dict(fn).get('meta') or '{}')
    naming=meta.get('naming') or {}
    if naming.get('method')=='sdk_signature':
        # Prioritize -O1 only for an identity recorded from SDK signature evidence.
        candidate=[('-O1' if flag.startswith('-O') else flag) for flag in flags]
        if not any(flag.startswith('-O') for flag in flags):candidate.append('-O1')
        out.append(candidate)
    return [f for f in out if f!=flags]
