"""Instruction evidence for small O32 runtime helpers and compiler-specialized MIPS calls."""
import re


def instructions(asm):
    return [line.split('*/',1)[-1].strip() for line in (asm or '').splitlines()
            if re.match(r'^(?:/\*.*?\*/\s*)?[a-z][a-z0-9.]*(?:\s|$)',line.strip())]


def helper(asm):
    code=instructions(asm)
    ops={line.split()[0] for line in code}
    if len(code)>64 or ops & {'jal','jalr','bal'}:
        return None
    if {'ddiv','mfhi','daddu','bgez','bgtz','blez','ld','sd'} <= ops:
        # O32 arguments are paired 32-bit words in the home slots, not N32/N64 registers.
        if all(re.search(r'\bsw\s+\$'+reg+r',',asm) for reg in ('a0','a1','a2','a3')):
            return 'signed_mod64'
    if {'cvt.l.d','ctc1','cfc1','dmfc1','dsll32','dsra32'} <= ops:
        if any(re.match(r'dsra32\s+\$v0,',line) for line in code) and any(re.match(r'dsra32\s+\$v1,',line) for line in code):
            return 'double_to_unsigned64'
    return None


def specialized_calls(asm):
    """Advisory only: a temporary live across a call may reflect whole-unit clobber analysis."""
    code=instructions(asm)
    for i,line in enumerate(code):
        if not re.match(r'jal\s+',line):
            continue
        # Require a prior definition and a later read before any redefinition, with no intervening
        # call or explicit spill/reload. Delay-slot definitions do not count as preserved values.
        for reg in (f'$t{n}' for n in range(10)):
            before=code[max(0,i-32):i]
            last_call=next((j for j in range(len(before)-1,-1,-1) if before[j].startswith(('jal ','jalr '))),-1)
            before=before[last_call+1:]
            if any(re.match(r's[wd]\s+'+re.escape(reg)+r',',x) for x in before):
                continue
            if not any(re.match(r'[a-z][a-z0-9.]*\s+'+re.escape(reg)+r',',x) and not x.startswith(('sw ','sd ','sb ','sh ')) for x in before):
                continue
            if re.search(re.escape(reg)+r'\b',code[i+1] if i+1<len(code) else ''):
                continue
            for after in code[i+2:i+12]:
                if after.startswith(('jal ','jalr ','jr ')):
                    break
                if re.match(r'[a-z][a-z0-9.]*\s+'+re.escape(reg)+r',',after) and not after.startswith(('sw ','sd ','sb ','sh ')):
                    break
                if re.search(re.escape(reg)+r'\b',after):
                    return True
    return False


def candidate(asm, name):
    """Small generic arithmetic hypotheses; no SDK/game source and no match verdict."""
    if not re.fullmatch(r'[A-Za-z_]\w*',name):
        return None
    kind=helper(asm)
    if kind=='signed_mod64':
        return ('long long '+name+'(long long a, long long b)\n{\n long long r;\n r=a%b;\n'
                ' if((r<0 && b>0)||(r>0 && b<0)) r+=b;\n return r;\n}\n')
    if kind=='double_to_unsigned64':
        return 'unsigned long long '+name+'(double value)\n{\n return (unsigned long long)value;\n}\n'
    return None
