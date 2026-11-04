"""Target-led fresh snapshots before byte stores; no global rename or speculative address inference."""
import re

from ..forks.csource import _blank
from . import branch_call_support as support
from .call_lifetime_repairs import block_path
from .integer_repair_support import IDENT, edit

FAMILY = 'typed_pointer_snapshot'
FAMILIES = (FAMILY,)
LIMIT = 2


def target_load_before_bytes(asm, symbol, arch):
    """Require the named target pointer load followed by byte stores in the same straight-line region."""
    if arch!='mips':return False
    # The source retains its existing global expression. A target's canonical relocation may
    # render the same load as another symbol plus an addend; this is load-order evidence only.
    for hit in re.finditer(r'\blw\s+[^\n]*%lo\([^)]+\)',asm):
        tail=[]
        for line in asm[hit.end():].splitlines()[1:18]:
            if re.search(r'\b(?:jal|jalr|j|jr|beq\w*|bne\w*|bgez\w*|bgtz\w*|blez\w*|bltz\w*)\b',line):break
            tail.append(line)
        if sum(bool(re.search(r'\bsb\b',line)) for line in tail)>=2:return True
    return False


def variants(source, name, header='', arch=None, asm=''):
    facts=support.facts(source,name,header,arch)
    if not facts or '__repair_pointer_snapshot' in source+header:return []
    part,flow,_=facts;context=_blank(header+'\n'+source);result=[]
    globals_=re.findall(r'\bextern\s+('+IDENT+r'\s*\*)\s*('+IDENT+r')\s*;',context)
    nodes=sorted(flow.nodes,key=lambda n:n.start)
    for typ,symbol in globals_:
        if not target_load_before_bytes(asm,symbol,arch):continue
        for pos,node in enumerate(nodes):
            store=re.fullmatch(rf'\s*({IDENT})->({IDENT})\s*=\s*{re.escape(symbol)}\s*;',part.clean[node.start:node.end])
            if not store:continue
            owner=store[1];before=[]
            for previous in reversed(nodes[max(0,pos-4):pos]):
                # Only constant byte-array writes on this same object; never cross a call or another load/write.
                byte=re.fullmatch(rf'\s*{re.escape(owner)}->({IDENT})\s*\[\s*\d+\s*\]\s*=\s*(0[xX][0-9a-fA-F]+|\d+)\s*;',part.clean[previous.start:previous.end])
                if not byte or int(byte[2],0)>255 or block_path(part,previous.start)!=block_path(part,node.start):break
                # The member must actually be a byte array in the owner's declared record.
                records=re.findall(r'\btypedef\s+struct(?:\s+'+IDENT+r')?\s*{([^{}]*)}\s*('+IDENT+r')\s*;',context,re.S)
                owners=re.findall(r'\b('+IDENT+r')\s*\*\s*'+re.escape(owner)+r'\s*(?=[;=])',part.body)
                if len(set(owners))!=1 or not any(t==owners[0] and re.search(r'\b(?:u8|unsigned\s+char)\s+'+re.escape(byte[1])+r'\s*\[',body) for body,t in records):break
                before.insert(0,previous)
            if len(before)<2:continue
            chain=before+[node]
            if any(part.clean[a.end:b.start].strip() for a,b in zip(chain,chain[1:])):continue
            first=before[0].start
            if part.clean[:first].rstrip()[-1:] not in ';{}':continue
            fresh='__repair_pointer_snapshot'
            # C89 declarations belong at block start; capture the pointer immediately before the byte stores.
            opening=block_path(part,first)[-1]
            candidate=edit(source,[(opening+1,opening+1,f'\n{typ} {fresh};\n'),
                                   (first,first,f'{fresh} = {symbol};\n'),
                                   (node.start,node.end,f'{owner}->{store[2]} = {fresh};')])
            result.append((FAMILY,candidate))
            if len(result)>=LIMIT:return result
    return result
