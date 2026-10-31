"""Suggest runtime-helper aliases only when every other compared instruction already agrees."""
import re

HELPERS = {'__ull_div','__ll_div','__ull_rem','__ll_rem','__ll_mul','__ll_lshift','__ull_rshift','__ll_rshift'}


def suggestions(rows):
    mappings={};count=0
    for row in rows or []:
        if len(row)!=3:return []
        kind,target,candidate=row
        if kind==' ':continue
        left=re.fullmatch(r'(jal|bl|call)\s+([A-Za-z_]\w*)',target.strip())
        right=re.fullmatch(r'(jal|bl|call)\s+\??([A-Za-z_]\w*)',candidate.strip())
        if not left or not right or left[1]!=right[1] or right[2] not in HELPERS or left[2]==right[2]:return []
        if right[2] in mappings and mappings[right[2]]!=left[2]:return []
        mappings[right[2]]=left[2];count+=1
    if not count:return []
    return ['Only runtime-helper call targets differ: '+', '.join(f'{a} → {b}' for a,b in sorted(mappings.items()))+
            '. Confirm the helper ABI and each target call site, then use an explicit typed call or a verified symbol alias. '
            'No addresses or symbols were changed; recompile and pass the byte judge before accepting it.']
