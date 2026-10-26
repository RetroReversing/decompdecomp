"""Generic C fixtures for captured repair families, independent of any game's source."""

from decompnet.app import draft_repairs, pattern_repairs
from decompnet.app.expression_repairs import variants as expression_variants


def test_branch_orientation_is_bounded_and_preserves_other_definition():
    source='int other(){return 7;} int f(int p){if(p == 0){a();}else{b();}return p;}'
    out=pattern_repairs.variants(source,'f')
    assert any('if(p != 0){b();}else{a();}' in v for _,v in out)
    assert all(v.startswith('int other(){return 7;}') for _,v in out)
    assert not pattern_repairs.variants('int f(int p){/* if(p==0){a();}else{b();} */return p;}','f')


def test_typed_temporaries_for_shifted_stores_and_call_arguments():
    source='void f(u16 *d,u32 w){ *(u16*)d=(u16)(w>>16); }'
    assert any('u16 __repair_narrow = (w>>16);' in v for _,v in pattern_repairs.variants(source,'f'))
    source='void f(void *p){ global=p; send(*(void **)((char *)p+24)); }'
    assert any('void * __repair_argument;' in v and '__repair_argument = *(void **)((char *)p+24);' in v and 'send(__repair_argument)' in v for _,v in pattern_repairs.variants(source,'f'))
    assert not any(f=='typed_temporary' for f,_ in pattern_repairs.variants('void f(void *p){ global=make(); send(*(void **)((char*)p+24)); }','f'))


def test_subtraction_mask_and_member_expression_get_narrowing_cast():
    source='int f(Obj *p,int n){return (p->position - n) & 0xFFFF;}'
    assert any('(unsigned short)(p->position - n)' in v for v in expression_variants(source,'f','mips'))
    assert not expression_variants('int f(int n){return (next() - n) & 0xFFFF;}','f','mips')


def test_debug_line_trials_preserve_tokens_and_require_debug_flags():
    source='void f(int x){\n int k;\n k=25;\n other=x+1;\n result=x*k;\n}'
    assert not any(f=='source_lines' for f,_ in pattern_repairs.variants(source,'f'))
    assert any('k=25;  result=x*k;' in v or 'k=25; result=x*k;' in v for _,v in pattern_repairs.variants(source,'f',True))
    assert any('k=25;' in v for v in draft_repairs.variants(source,'f','mips',flags=['-g3']))
    assert not any('k=25; result=x*k;' in v for v in draft_repairs.variants(source,'f','mips',flags=['-g0']))


def test_argument_refinement_precedes_large_candidate_queue():
    source='void f(void *arg1) { send(arg1); }'
    asm='glabel f\nlw $a1, 0($a1)\njal send\nnop\njr $ra\nnop'
    # Plain saved C can need the same missing-position refinement as an m2c macro draft.
    out=draft_repairs.variants(source,'f','mips',asm)
    assert out and "s32 arg0" in out[0]



def test_reuses_compatible_local_without_losing_later_assignment():
    source='void f(void *p){void *q; global=p; send(*(void **)((char*)p+24)); q=other; use(q);}'
    out=pattern_repairs.variants(source,'f')
    assert any('q = *(void **)((char*)p+24);' in v and 'send(q)' in v and 'q=other;' in v for _,v in out)


def test_narrow_copy_coalescing_stays_within_requested_definition():
    source='int f(Obj *p){s32 wide; u16 small; wide=read();small=wide; use(small);return (p->position-wide)&0xFFFF;} int other(){return 9;}'
    assert any('u16 wide;' in v and 'use(wide)' in v and '(u16)(p->position-wide)' in v for v in expression_variants(source,'f','mips'))
    assert all(v.endswith('int other(){return 9;}') for v in expression_variants(source,'f','mips'))
