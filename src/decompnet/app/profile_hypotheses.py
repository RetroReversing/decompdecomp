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
    from .sdk_io_repairs import io_target
    if naming.get('method')=='sdk_signature' or io_target(dict(fn).get('asm','')):
        # Prioritize -O1 only for an identity recorded from SDK signature evidence.
        candidate=[('-O1' if flag.startswith('-O') else flag) for flag in flags]
        if not any(flag.startswith('-O') for flag in flags):candidate.append('-O1')
        out.append(candidate)
    return [f for f in out if f!=flags]


def neighbours(net, proj, fn, own):
    """Nearby exact matches propose flags, not an inferred compiler range or a setting change."""
    from ..project import compiler_for
    rows=net.store.all("SELECT vram FROM functions WHERE project_id=? AND status='matched' AND best_score=0 "
                       "AND vram BETWEEN ? AND ? ORDER BY ABS(vram-?) LIMIT 8",
                       proj['id'],fn['vram']-16384,fn['vram']+16384,fn['vram'])
    cfg=net.cfg(proj);ranked=[]
    for row in rows:
        profile=compiler_for(cfg,row['vram'])
        flags=list(profile.get('flags',[]))
        if profile.get('toolchain')==own.get('toolchain') and not profile.get('cmd') and flags!=list(own['flags']) and flags not in ranked:
            ranked.append(flags)
    return ranked[:2]
