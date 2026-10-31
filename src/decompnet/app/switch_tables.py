"""Read-only target jump-table ordering; reorder whole case groups, preserving fallthrough."""
import ast
import re

from ..forks.csource import _blank, find
from .pattern_repairs import close

LABEL = re.compile(r"\bcase\s+('(?:\\.|[^'\\])'|-?(?:0[xX][0-9a-fA-F]+|\d+))\s*:|\bdefault\s*:")


def value(text):
    return ord(ast.literal_eval(text)) if text.startswith("'") else int(text, 0) if 'x' in text.lower() else int(text)


def variants(source, name, tables=()):
    """tables contain (minimum case value, destination addresses). No guessed table earns a match."""
    span = find(source, name)
    if not span:
        return []
    clean = _blank(source); result=[]
    for switch in re.finditer(r'\bswitch\s*\(', clean[span[0]:span[1]]):
        cond=close(clean,span[0]+switch.end()-1)
        if not cond:
            continue
        brace=cond+len(clean[cond:])-len(clean[cond:].lstrip())
        if clean[brace:brace+1]!='{':
            continue
        end=close(clean,brace,'{','}')
        if not end:
            continue
        labels=[m for m in LABEL.finditer(source,brace+1,end-1)
                if clean[brace+1:m.start()].count('{')==clean[brace+1:m.start()].count('}')
                and clean[m.start():m.start()+4] in ('case','defa')]
        all_cases=[m for m in re.finditer(r'\b(?:case|default)\b',clean[brace+1:end-1])
                   if clean[brace+1:brace+1+m.start()].count('{')==clean[brace+1:brace+1+m.start()].count('}')]
        if len(all_cases)!=len(labels):
            continue
        if not 2<=len(labels)<=64 or clean[brace+1:labels[0].start()].strip():
            continue
        # Move all fallthrough-connected cases together, including grouped labels and default.
        groups=[];start=labels[0].start();values=[];default=False
        for i,label in enumerate(labels):
            stop=labels[i+1].start() if i+1<len(labels) else end-1
            if label[1] is None: default=True
            else: values.append(value(label[1]))
            body=clean[label.end():stop].strip()
            if not body:
                continue
            # A terminal unconditional goto/return/break at case depth, not a nested if body.
            terminal=re.search(r'\b(?:goto\s+\w+|break|continue|return(?:\s+[^;{}]+)?)\s*;\s*$',body)
            prefix=body[:terminal.start()].rstrip() if terminal else ''
            if terminal and (not prefix or prefix[-1] in ';}') and prefix.count('{')==prefix.count('}'):
                groups.append((start,stop,values,default));start=stop;values=[];default=False
        if start!=end-1 or len(groups)<2:
            continue
        # Cross-case labels and unscoped local declarations can change scope when moved.
        if any(any(clean[a:a+m.start()].count('{') == clean[a:a+m.start()].count('}')
                   for m in re.finditer(r'\b(?:char|int|short|long|float|double|[su]\d+)\s+\**\w+\s*(?:[=;\[])',clean[a:b]))
               for a,b,_,_ in groups):
            continue
        for low,destinations in tables:
            from collections import Counter
            default_address=Counter(destinations).most_common(1)[0][0]
            addresses=[]
            for _,_,numbers,isdefault in groups:
                if (not numbers and not isdefault) or any(not 0<=n-low<len(destinations) for n in numbers):
                    break
                group={destinations[n-low] for n in numbers}
                if isdefault: group.add(default_address)
                # Fallthrough groups may have distinct entry points; their physical order is the lowest.
                addresses.append(min(group))
            else:
                order=sorted(range(len(groups)),key=lambda i:addresses[i])
                if order!=list(range(len(groups))):
                    text=''.join(source[groups[i][0]:groups[i][1]] for i in order)
                    result.append(('target_switch_order',source[:groups[0][0]]+text+source[groups[-1][1]:]))
    return list(dict.fromkeys(result))[:2]


def read(net, proj, fn):
    """MIPS assembly records the lower case bound and table address; other ISAs keep generic repairs."""
    if proj['arch']!='mips' or not getattr(net,'is_rom',lambda p:False)(proj):
        return []
    asm=fn['asm'] or ''; result=[]
    for hit in re.finditer(r'%hi\((jtbl_[A-Za-z0-9_]+)\)',asm):
        prefix=asm[max(0,hit.start()-700):hit.start()]
        bound=list(re.finditer(r'addiu\s+\$\w+,\s*\$\w+,\s*(-0x[0-9A-Fa-f]+)',prefix))
        length=list(re.finditer(r'sltiu\s+\$\w+,\s*\$\w+,\s*(0x[0-9A-Fa-f]+|\d+)',prefix))
        if not bound or not length:
            continue
        low=-int(bound[-1][1],0);count=int(length[-1][1],0)
        address=net.symbols(proj['id']).resolve(hit[1])
        if address is None or not 2<=count<=256:
            continue
        data=net.reader(proj,fn)(address,count*4)
        if data is None:
            # Some basic imports describe code ranges only. A single consistent linear image
            # mapping can propose a nearby data table; ambiguous/overlay mappings are excluded.
            # Destination bounds below and the ordinary exact judge remain mandatory.
            cfg=net.cfg(proj);segments=cfg.get('segments') or []
            biases={s['vram']-s['rom_start'] for s in segments}
            last=max((s['rom_end'] for s in segments),default=0)
            if len(biases)==1 and last:
                offset=address-next(iter(biases))
                if last<=offset and offset+count*4<=last+65536:
                    image,_=net._rom(proj)
                    if offset+count*4<=len(image):data=image[offset:offset+count*4]
        if data is not None and len(data)==count*4:
            order='big' if proj['big_endian'] else 'little'
            destinations=[int.from_bytes(data[i:i+4],order) for i in range(0,len(data),4)]
            if all(fn['vram']<=n<fn['vram']+fn['size'] for n in destinations):
                result.append((low,destinations))
    return result
