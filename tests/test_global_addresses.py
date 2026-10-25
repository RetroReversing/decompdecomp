import pytest

from decompnet.app.address_repairs import variants
from decompnet.app.global_addresses import normalize, recover


def report(middle='nop', op='lw', low='24'):
    return {'rows': [('i','lui $v0, %hi(D_80000000)','lui $v0, %hi(D_80000000+16)'),
                     (' ',middle,middle),
                     ('i',f'{op} $a0, {low}($v0)',f'{op} $a0, %lo(D_80000000+16)($v0)')]}


@pytest.mark.parametrize('op',['lw','sw','lb','lbu','lh','lhu','sb','sh'])
def test_nonadjacent_memory_low_halves(op):
    r=report('addu $a1, $a2, $a3',op)
    assert variants('return D_80000000[4];',r,{},True,set()) == ['return D_80000000[6];']
    assert r['rows'][2][1].endswith('24($v0)')


@pytest.mark.parametrize('middle',['jal fn','beq $a0, $a1, label','move $v0, $a1','unknown $a0'])
def test_control_flow_overwrites_and_unknown_instructions_stop_tracking(middle):
    assert variants('D_80000000[4]',report(middle),{},True,set()) == []


def test_signed_low_carry_and_no_feedback_mutation():
    r=report(low='-16')
    r['rows'][0]=('i','lui $v0, %hi(D_80000000+65536)','lui $v0, %hi(D_80000000+16)')
    assert 'D_80000000+65520' in normalize(r)['rows'][-1][1]
    assert variants('D_80000000 + 16',r,{},True,set()) == ['D_80000000 + 65520']


def test_multiple_consumers_preserve_conflicting_evidence():
    r=report(op='sw')
    r['rows'].append(('i','sw $a1, 32($v0)','sw $a1, %lo(D_80000000+16)($v0)'))
    assert variants('D_80000000[4]',r,{},True,set()) == []


def test_load_clobbers_base_but_store_does_not():
    r=report(op='lw');r['rows'][2]=('i','lw $v0, 24($v0)','lw $v0, %lo(D_80000000+16)($v0)')
    r['rows'].append(('i','lw $a1, 32($v0)','lw $a1, %lo(D_80000000+16)($v0)'))
    assert len(recover(r['rows'],1)) == 1


def test_unaligned_rows_and_unresolved_symbols_are_ignored():
    r=report();r['rows'][1]=('<','nop','')
    assert not recover(r['rows'],1)
    r=report();r['rows']=[(m,a.replace('D_80000000','unknown'),b) for m,a,b in r['rows']]
    assert not recover(r['rows'],1)


def test_zero_low_does_not_replace_register_digits():
    r=report(low='0')
    assert normalize(r)['rows'][-1][1] == 'lw $a0, %lo(D_80000000)($v0)'


def test_fully_symbolic_pairs_keep_their_original_base_labels():
    r={'rows':[('i','lui $v0, %hi(D_80000000)','lui $v0, %hi(D_80010000)'),
               ('i','lw $a0, %lo(D_80000020)($v0)','lw $a0, %lo(D_80010030)($v0)')]}
    assert normalize(r) == r
