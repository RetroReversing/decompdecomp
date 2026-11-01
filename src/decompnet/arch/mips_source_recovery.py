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
