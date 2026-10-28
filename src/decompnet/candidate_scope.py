"""Build evidence local to a function, shared by cooldowns and persistent trial history."""
import hashlib
import json
import re
from pathlib import Path

from .forks.project import ForkError
from .project import compiler_for


def digest(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return 'unavailable'


def dependencies(root, source):
    """Follow quoted source includes; unresolved/system includes conservatively watch include/."""
    pending=[root/source] if source else []
    seen={};fallback=False
    while pending and len(seen)<256:
        path=pending.pop()
        if path in seen:
            continue
        seen[path]=digest(path)
        try:
            text=path.read_text(errors='replace')
        except OSError:
            fallback=True
            continue
        for delimiter,name in re.findall(r'^\s*#\s*include\s*(["<])([^">]+)[">]',text,re.M):
            choices=[path.parent/name,root/'include'/name,root/name]
            found=next((p for p in choices if p.is_file() and p.resolve().is_relative_to(root.resolve())),None)
            if found is not None:
                pending.append(found)
            else:
                fallback=True
    if pending or fallback:
        seen.update((p,digest(p)) for p in (root/'include').rglob('*.h'))
    return sorted((str(p),value) for p,value in seen.items())


def state(net,proj,fn):
    cfg=json.loads(proj['config'])
    build={key:cfg.get(key) for key in ('kind','platform','big_endian','gp')}
    build['compiler']=compiler_for(cfg,fn['vram']) if cfg.get('compiler') else None
    ctx=net._context(proj['id'],fn['unit'])
    text='\n'.join(str(fn[key] or '') for key in ('name','asm','best_source','seed','relocs'))
    names=set(re.findall(r'\b[A-Za-z_]\w*\b',text))
    addresses={int(x,16) for x in re.findall(r'\b0x([0-9a-fA-F]+)\b',text)}
    addresses.update(int(x,16) for x in re.findall(r'\b(?:D_|func_)([0-9a-fA-F]{8})\b',text))
    if fn['vram'] is not None:
        addresses.add(fn['vram'])
    symbols=[tuple(r) for r in net.store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? '
             'AND (name IN (SELECT value FROM json_each(?)) OR address IN (SELECT value FROM json_each(?))) ORDER BY name',
             proj['id'],json.dumps(sorted(names)),json.dumps(sorted(addresses)))]
    result=[build,proj['root'],proj['arch'],proj['big_endian'],ctx['header'] if ctx else None,symbols,fn['target'],fn['relocs'],fn['vram']]
    if getattr(net,'fork_slug',lambda _:None)(proj['slug']):
        try:
            _,fp,_=net.forks.get(proj['slug']);unit=fp.unit(fn['unit'])
            result += [repr(unit), dependencies(fp.root,unit.source),
                       [(name,digest(fp.root/name)) for name in ('Makefile','build.ninja','objdiff.json','configure.py')]]
        except (ForkError,OSError):
            result.append(('unavailable-project',digest(Path(proj['root'])/'objdiff.json')))
    return result


def key(net,proj,fn):
    return hashlib.sha256(repr(state(net,proj,fn)).encode()).hexdigest()
