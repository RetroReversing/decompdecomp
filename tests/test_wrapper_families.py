import json
from types import SimpleNamespace

import pytest

from decompnet.app import wrapper_families as wrappers


def fn(address,name='f',id=1,big=True):
    words=[0x27bdffe8,0x00c02021,0x3c060000|((address+0x8000)>>16),0x24c60000|(address&65535),
           0xafbf0010,0x0c100f4d,0,0x8fbf0010,0x24020001,0x03e00008,0x27bd0018]
    return {'id':id,'name':name,'target':b''.join(w.to_bytes(4,'big' if big else 'little') for w in words),
            'relocs':json.dumps([[8,5,address],[12,6,address],[20,4,0x80403d34]]),'size':44}


@pytest.mark.parametrize('big',[False,True])
def test_grouping_changes_only_one_paired_address_with_identical_callee_and_instruction_shape(big):
    a,b=fn(0x414f00,big=big),fn(0x415d90,big=big)
    assert wrappers.shape(a,big)[0]==wrappers.shape(b,big)[0]
    changed=dict(b,target=b['target'][:-4]+(0).to_bytes(4,'big' if big else 'little'))
    assert wrappers.shape(a,big)[0]!=wrappers.shape(changed,big)[0]
    bad=dict(b,relocs='[[8,5,1],[12,6,2],[20,4,3]]')
    assert wrappers.shape(bad,big) is None


def test_verified_wrapper_c_is_instantiated_with_low_address_local_substitution_only():
    donor=fn(0x414f00,'old');dest=fn(0x415d90,'new',2)
    donor['matched_source']='extern int D_414F00; void helper(int,int,int*); int old(int a,int b,int c){helper(c,b,&D_414F00);return 1;}'
    symbols=SimpleNamespace(resolve=lambda name:0x414f00 if name=='D_414F00' else None,label=lambda address:'D_415D90')
    net=SimpleNamespace(symbols=lambda pid:symbols)
    group={wrappers.shape(donor,True)[0]:[donor]}
    rows=wrappers.candidates(net,{'id':1,'big_endian':1},dest,group)
    assert len(rows)==1 and rows[0][0]=='wrapper-family'
    assert 'extern int D_415D90;' in rows[0][2] and 'int new(' in rows[0][2]
    assert 'helper(c,b,&D_415D90)' in rows[0][2]
    donor['matched_source']=donor['matched_source'].replace('&D_414F00','D_414F00')
    assert not wrappers.candidates(net,{'id':1,'big_endian':1},dest,group)
