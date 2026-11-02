"""MIPS operand evidence for bounded private source hypotheses, never a match verdict."""
import re

NUMBER = r'-?(?:0x[0-9a-fA-F]+|\d+)'


def operand_changes(rows):
    result=[]
    for i,(mark,target,own) in enumerate(rows):
        if mark==' ':continue
        a=re.fullmatch(r'((?:lb|lbu|lh|lhu|lw|lwc1|ldc1|sb|sh|sw|swc1|sdc1)\s+[^,]+,\s*)('+NUMBER+r')(\(\$[^)]+\))',target)
        b=re.fullmatch(r'((?:lb|lbu|lh|lhu|lw|lwc1|ldc1|sb|sh|sw|swc1|sdc1)\s+[^,]+,\s*)('+NUMBER+r')(\(\$[^)]+\))',own)
        if a and b and (a[1],a[3])==(b[1],b[3]) and '$sp' not in a[3]:
            result.append({'row':i,'kind':'field','old':int(b[2],0),'new':int(a[2],0),'store':target.startswith('s')})
            continue
        a=re.fullmatch(r'((?:sltiu?|addiu|ori|andi|xori)\s+[^,]+,\s*[^,]+,\s*)('+NUMBER+r')',target)
        b=re.fullmatch(r'((?:sltiu?|addiu|ori|andi|xori)\s+[^,]+,\s*[^,]+,\s*)('+NUMBER+r')',own)
        if a and b and a[1]==b[1] and '$sp' not in a[1]:
            result.append({'row':i,'kind':'constant','old':int(b[2],0),'new':int(a[2],0)})
    return result


def blocks(asm, starts, end):
    """Only real target instructions between known jump-table entry addresses."""
    instructions=[]
    for line in asm.splitlines():
        m=re.search(r'/\*\s*[0-9A-Fa-f]+\s+([0-9A-Fa-f]{8})\s+[0-9A-Fa-f]{8}\s*\*/\s*(.+)',line)
        if m:instructions.append((int(m[1],16),m[2].strip()))
    result={}
    boundaries=sorted(set(starts)|{end})
    for a,b in zip(boundaries,boundaries[1:]):
        ins=[text for address,text in instructions if a<=address<b]
        if not ins:continue
        symbols=re.findall(r'\bD_[0-9A-Fa-f]{8}\b','\n'.join(ins))
        # Structural cloning is restricted to terminating arms with the same instructions,
        # ABI registers and constants. Only symbolic data addresses may differ.
        if not any(re.match(r'b\s+|jr\s+\$ra',text) for text in ins):continue
        registers={}
        def reg(m, registers=registers):
            n=m[0]
            if not re.fullmatch(r'\$(?:t[0-9]|s[0-7]|at)',n):return n
            return registers.setdefault(n,'$tmp'+str(len(registers)))
        signature=re.sub(r'\$\w+',reg,'\n'.join(ins))
        signature=re.sub(r'\bD_[0-9A-Fa-f]{8}\b','DATA',signature)
        signature=re.sub(r'\.L[0-9A-Fa-f]+','EXIT',signature)
        result[a]={'symbols':list(dict.fromkeys(symbols)), 'signature':signature,
                   'calls':re.findall(r'\bjal\s+(\w+)','\n'.join(ins))}
    return result


def recovery_hints(asm, rows):
    """Classify evidence for review, not proof of a private ABI or a reason to alter boundaries."""
    result=[]
    text='\n'.join(line.split('*/',1)[-1].strip() for line in asm.splitlines())
    if re.search(r'\bjal\b',text) and re.search(r'\$(?:a[0-3]|t[0-9])',text):
        # A retained argument register after a call without a corresponding reload/copy.
        for m in re.finditer(r'\bjal\s+\w+\n[^\n]*\n',text):
            tail=text[m.end():]
            if re.search(r'\([^)]*\$a3\)',tail) and not re.search(r'\b(?:lw|or|move|addiu)\s+\$a3\b',tail):
                result.append('Possible internal-call ABI: target keeps an argument pointer in $a3 across a call. Try the original translation unit and internal helper definitions; isolated-source permutations may not reproduce compiler clobber knowledge.')
                break
    if '$fa0' in text or '$f12' in text:
        if re.search(r'\blw\s+\$\w+,\s*'+NUMBER+r'\(\$a0\)',text) and not re.search(r'\b(?:mtc1|jal)\b',text):
            result.append('Possible private pointer/float entry ABI: target consumes $a0 and incoming $f12 without a transfer. Check original static helper and translation-unit context before changing the signature.')
    marks={r[0] for r in rows if r[0]!=' '}
    if marks and marks<={'r','s'}:
        result.append('Register/frame-only mismatch: prioritize bounded scheduling/context trials; unrelated retail control-flow edits are unsupported.')
    return result


def tail_branch(asm):
    """One exact terminal conditional-clear/flag-set shape; no arbitrary instruction lifting."""
    lines=[line.split('*/',1)[-1].strip() for line in asm.splitlines() if '*/' in line]
    reg=r'(\$\w+)';off='('+NUMBER+')'
    for i in range(len(lines)-7):
        a=re.fullmatch(r'lw\s+'+reg+r',\s*'+off+r'\('+reg+r'\)',lines[i])
        if not a:continue
        branch=re.fullmatch(r'beql\s+'+re.escape(a[1])+r',\s*\$zero,\s*(\.L[0-9A-Fa-f]+)',lines[i+1])
        if not branch or not re.fullmatch(r'lw\s+\$ra,\s*'+NUMBER+r'\(\$sp\)',lines[i+2]):continue
        clear=re.fullmatch(r'sw\s+\$zero,\s*'+off+r'\('+re.escape(a[3])+r'\)',lines[i+3])
        load=re.fullmatch(r'lw\s+'+reg+r',\s*'+off+r'\('+reg+r'\)',lines[i+4])
        flag=re.fullmatch(r'ori\s+'+reg+r',\s*'+reg+r',\s*'+off,lines[i+5])
        if not clear or not load or not flag or flag[2]!=load[1]:continue
        if not re.fullmatch(r'sw\s+'+re.escape(flag[1])+r',\s*'+re.escape(load[2])+r'\('+re.escape(load[3])+r'\)',lines[i+6]):continue
        # The taken path must be precisely the function epilogue, not another body.
        label=asm.find(branch[1]+':')
        if label<0:continue
        tail=[s.split('*/',1)[-1].strip() for s in asm[label:].splitlines() if '*/' in s]
        if len(tail)!=3 or not re.fullmatch(r'addiu\s+\$sp,\s*\$sp,\s*'+NUMBER,tail[0]) or not re.fullmatch(r'jr\s+\$ra',tail[1]) or tail[2]!='nop':continue
        # Trace only direct argument copies and explicit stack saves/restores through calls.
        origins={f'$a{n}':n for n in range(4)};slots={};pending=False
        for line in lines[:i]:
            prior=pending;pending=False
            move=re.fullmatch(r'or\s+'+reg+r',\s*'+reg+r',\s*\$zero',line)
            save=re.fullmatch(r'sw\s+'+reg+r',\s*'+off+r'\(\$sp\)',line)
            restore=re.fullmatch(r'lw\s+'+reg+r',\s*'+off+r'\(\$sp\)',line)
            if move:origins[move[1]]=origins.get(move[2])
            elif save:slots[int(save[2],0)]=origins.get(save[1])
            elif restore:origins[restore[1]]=slots.get(int(restore[2],0))
            elif re.match(r'jal\s+',line):pending=True
            elif re.match(r'(?:l\w+|addiu|addu|ori|andi|xori|sll)\s+',line):
                dst=re.search(r'\$\w+',line)
                if dst:origins.pop(dst[0],None)
            if prior:
                origins={k:v for k,v in origins.items() if not re.fullmatch(r'\$(?:a[0-3]|t[0-9]|v[01]|at)',k)}
        p,q=origins.get(a[3]),origins.get(load[3])
        values=[int(a[2],0),int(clear[1],0),int(load[2],0),int(flag[3],0)]
        if p is None or q is None or p==q or any(v<0 or v>65535 for v in values) or not values[-1]:continue
        return {'condition_arg':p,'object_arg':q,'test':values[0],'clear':values[1],'flags':values[2],'mask':values[3],
                'instructions':lines[i:i+7]}
    return None
