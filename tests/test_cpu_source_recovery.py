"""CPU hypotheses need specific evidence; publication still requires ordinary exact byte/data judging."""
import json
from types import SimpleNamespace

import pytest

from decompnet.app import cpu_source_context as contexts
from decompnet.app import cpu_source_recovery as recovery
from decompnet.app import cpu_source_repairs as repairs
from decompnet.app import cpu_switch_repairs as switches
from decompnet.app import prepared_evidence as bundles
from decompnet.app import source_preparation as prep
from decompnet.arch import mips_source_recovery as mips
from decompnet.network import Agent, NetworkError
from tests.test_prepared_evidence import prepared_net


def test_actual_aggregate_prototype_and_target_call_macro_restored():
    source='void f(Item *p){ fetch(p); }'
    header='typedef struct {float x,y,z;} Vec;\nfloat fetch(int p);\n#define fetch(p) ((p)->pos)\n'
    reference='Vec fetch(Item *p){return p->pos;}'
    out=contexts.variants(source,header,reference,['fetch'])
    assert any('Vec fetch(Item *p);' in h for _,_,h in out)
    assert any('#define fetch' not in h and 'Vec fetch(Item *p);' in h for _,_,h in out)
    assert not contexts.variants(source,header,'float fetch(int p);',[])  # same ABI, no target call evidence
    assert not contexts.variants(source,'int (*fetch)(int);','Vec fetch(Item *p);',[])
    assert not contexts.declarations('int fetch(int); float fetch(float);',{'fetch'})


def test_verified_records_transfer_dependencies_and_reject_incompatible_scalar_alias():
    a='typedef int Word; typedef struct {Word n;} Child; typedef struct {Child c;} Item;'
    b='typedef int Word; typedef struct {Word n, extra;} Child; typedef struct {Child c; int tail;} Item;'
    got=contexts.merge_records('void f(Item *p){}',a,b)
    assert 'extra' in got and 'tail' in got
    assert contexts.merge_records('void f(Item *p){}',a,b.replace('typedef int Word','typedef float Word')) is None
    assert contexts.merge_records('void f(int p){}',a,b) is None


def mapped(rows, lines):
    return {'lines':[{'line':line,'instructions':[{'row':i}]} for i,line in enumerate(lines)]}


def test_consistent_record_gap_uses_two_mapped_fields_and_keeps_other_records():
    source='void f(App *p){\np->a=1;\np->b=2;\n}'
    header='typedef struct {int x;} Other; typedef struct {int first; int a; int b;} App;'
    rows=[('i','sw $t0, 8($a0)','sw $t0, 4($a0)'),('i','sw $t1, 12($a0)','sw $t1, 8($a0)')]
    out=repairs.variants(source,header,'mips',rows,mapped(rows,[2,3]))
    assert len(out)==1 and 'int first; unsigned char cpu_layout_gap[4];  int a;' in out[0][2]
    assert 'typedef struct {int x;} Other;' in out[0][2]
    assert not repairs.variants(source,header,'mips',rows,{})
    assert not repairs.variants(source,header,'ppc',rows,mapped(rows,[2,3]))
    assert not repairs.variants(source,header,'mips',rows[:1],mapped(rows,[2]))
    assert not repairs.variants(source,header.replace('int a; int b;','int a,b;'),'mips',rows,mapped(rows,[2,3]))


def test_retail_constants_and_extent_can_be_tried_coherently():
    source='void f(int n){\nif(n == LIMIT) use(n);\nfor(i=0;i<sizeof(codes)/sizeof(Code);i++) use(i);\n}'
    header='#define LIMIT 16\nextern Code codes[32];\n'
    rows=[('i','addiu $at, $zero, 18','addiu $at, $zero, 16'),('i','slti $at, $t0, 36','slti $at, $t0, 32')]
    out=repairs.variants(source,header,'mips',rows,mapped(rows,[2,3]))
    assert any('#define LIMIT 18' in h and 'codes[36]' in h for _,_,h in out)
    assert not repairs.variants(source,header,'mips',rows,{})
    assert not repairs.variants(source.replace('if(n == LIMIT)','use(n + LIMIT)'),header,'mips',rows[:1],mapped(rows,[2]))


def test_reference_line_directive_maps_to_physical_source(monkeypatch):
    from decompnet import source_lines
    seen=[]
    monkeypatch.setattr(source_lines,'mismatches',lambda *a,**k: seen.append(k) or {})
    repairs.locations(b'object','f',[], '#line 1400 "reference.c"\nvoid f(void){}')
    assert seen[0]['first_line']==1399 and seen[0]['filename']=='reference.c'
    assert repairs.locations(b'object','f',[], '#line 1 "a"\n#line 2 "b"\n')=={}


def test_retail_switch_uses_real_symbols_and_only_supported_clones():
    s='void f(int n){switch(n){case 0: show(D_80001000); break; default: fail(); break;}}'
    blocks={100:{'symbols':['D_80001000'],'calls':['show'],'signature':'same'},
            120:{'symbols':['D_80002000'],'calls':['show'],'signature':'same'},
            140:{'symbols':[],'calls':['fail'],'signature':'different'}}
    out=switches.variants(s,'f','',[(0,[100,120,140,140])],blocks)
    assert len(out)==1 and 'case 1:' in out[0][1] and 'show(D_80002000)' in out[0][1]
    assert 'default: fail(); break;' in out[0][1]
    assert 'case 2:' not in out[0][1]
    blocks[120]['signature']='extra store'
    assert not any('show(D_80002000)' in x[1] for x in switches.variants(s,'f','',[(0,[100,120,140,140])],blocks))
    assert not switches.variants(s.replace('D_80001000','unknown'),'f','',[(0,[100,120])],blocks)


def test_mips_blocks_keep_abi_registers_and_constants_distinct():
    a='/* 0 80000100 00000000 */ lui $a1, %hi(D_80001000)\n/* 4 80000104 00000000 */ jal show\n/* 8 80000108 00000000 */ b .L80000200\n/* C 8000010C 00000000 */ nop\n'
    b=a.replace('8000010','8000012').replace('D_80001000','D_80002000')
    got=mips.blocks(a+b,[0x80000100,0x80000120],0x80000140)
    assert got[0x80000100]['signature']==got[0x80000120]['signature']
    changed=mips.blocks(a+b.replace('$a1','$a2'),[0x80000100,0x80000120],0x80000140)
    assert changed[0x80000100]['signature']!=changed[0x80000120]['signature']


def test_followup_queue_is_bounded_and_has_no_unbounded_recursion(monkeypatch):
    monkeypatch.setattr(recovery,'proposals',lambda *a:[('cpu-test','s'+str(i),'h') for i in range(30)])
    variants=[('s','h')];origins={variants[0]:{'family':'original'}}
    recovery.extend(None,None,None,variants,origins,'s','h',{},origins[variants[0]])
    assert len(variants)==7
    recovery.extend(None,None,None,variants,origins,'s','h',{}, {'cpu_depth':2})
    assert len(variants)==7


@pytest.mark.parametrize('verdict',[False,None])
def test_automatic_bundle_requires_true_linked_byte_data_verdict(monkeypatch,verdict):
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net._function=lambda *a:net.store.one('SELECT * FROM functions WHERE id=1')
    net.project_row=lambda s:net.store.one('SELECT * FROM projects WHERE id=1')
    net._compile=lambda *a:SimpleNamespace(ok=True,obj=b'object',effective_settings={})
    net._evaluate=lambda *a:('candidate',verdict,[])
    monkeypatch.setattr('decompnet.rom.export._single_definition',lambda *a:True)
    before=prep.inputs(net,proj,fn)
    with pytest.raises(NetworkError,match='linked-byte/data'):
        bundles.promote_measured(net,proj,fn,'s','h',{'toolchain':'ido-5.3','flags':['-O3']},Agent(1,'autopilot',None),before)
    assert net.store.one('SELECT status FROM functions')['status']=='open'


def test_automatic_bundle_recompiles_then_atomically_publishes(monkeypatch):
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net._function=lambda *a:net.store.one('SELECT * FROM functions WHERE id=1')
    net.project_row=lambda s:net.store.one('SELECT * FROM projects WHERE id=1')
    calls=[]
    net._compile=lambda *a:calls.append(a) or SimpleNamespace(ok=True,obj=b'object',effective_settings={})
    net._evaluate=lambda *a:('candidate',True,[])
    monkeypatch.setattr('decompnet.rom.export._single_definition',lambda *a:True)
    settings={'toolchain':'ido-5.3','flags':['-O3']}
    assert bundles.promote_measured(net,proj,fn,'new','context',settings,Agent(1,'autopilot',None),prep.inputs(net,proj,fn))
    assert len(calls)==1 and net.store.one('SELECT status FROM functions')['status']=='matched'
    assert json.loads(net.project_row('game')['config'])['compiler_ranges'][0]['flags']==['-O3']
    assert net._context(1,net.store.one('SELECT unit FROM functions')['unit'])['header']=='context'


def test_auto_exact_is_opt_in_and_imported_builds_excluded():
    net,proj,fn=prepared_net()
    assert not bundles.auto_existing(net,proj,fn,Agent(1,'autopilot',None))
    net.is_rom=lambda p:True;net.fork_slug=lambda s:'build-owned'
    with pytest.raises(NetworkError,match='local ROM'):
        bundles.promote_measured(net,proj,fn,'s','h',{},None,None)


def test_failed_auto_recheck_is_scoped_and_does_not_starve_new_source(monkeypatch):
    net,proj,fn=prepared_net()
    with net.store.tx() as db:
        db.execute('INSERT INTO settings(key,value) VALUES(?,?)',('source-preparation:1',json.dumps({'auto_exact':True})))
        item={'exact':True,'toolchain':'ido-5.3','flags':['-O3'],'source':'old candidate','header':'old header'}
        db.execute('INSERT INTO reuse_trials(function_id,signature,outcome,reason,feedback) VALUES(1,?,?,?,?)',
                   ('local-source-profile-v2:exact','0','test',json.dumps(item)))
    calls=[]
    monkeypatch.setattr(bundles,'digest',lambda *a:'scope1')
    def fail(*args):calls.append(args);raise NetworkError('data differs')
    monkeypatch.setattr(bundles,'promote_measured',fail)
    assert bundles.auto_existing(net,proj,fn,None)['matched'] is False
    assert bundles.auto_existing(net,proj,fn,None) is None and len(calls)==1
    monkeypatch.setattr(bundles,'digest',lambda *a:'scope2')
    assert bundles.auto_existing(net,proj,fn,None)['tested']==1 and len(calls)==2


def test_cached_measurements_regenerate_followups_on_a_later_pass(monkeypatch,tmp_path):
    from decompnet.app.permute import Permuter, State
    from tests.test_source_preparation import config
    net,proj,fn=prepared_net();net.links={};net.fork_slug=lambda s:None
    net.local_agent=lambda n:(Agent(1,'autopilot',None),'')
    net.project_row=lambda s:net.store.one('SELECT * FROM projects WHERE id=1')
    net.cfg=lambda p:json.loads(p['config'])
    with net.store.tx() as db:db.execute('DELETE FROM claims')
    cfg=config(tmp_path);(tmp_path/'game.c').write_text('int original(void){return 1;}');(tmp_path/'types.h').write_text('')
    prep.configure(net,proj,cfg)
    calls=[]
    def attempt(*args):
        calls.append(args[3]);args[-1].update(rows=[['i','a','b']],lines={});return 100
    monkeypatch.setattr(prep,'attempt',attempt)
    monkeypatch.setattr(prep,'private_profiles',lambda *a:None)
    monkeypatch.setattr(recovery,'proposals',lambda net,p,f,s,h,m:[('cpu-test',s+'\n/* trial */',h)] if m else [])
    permuter=Permuter(net)
    first=prep.run(permuter,proj,State(),budget=1)
    assert first['tested']==1 and len(calls)==1
    second=prep.run(permuter,proj,State(),budget=1)
    assert second['tested']==1 and len(calls)==2 and '/* trial */' in calls[-1]


def test_layout_mapping_selects_written_record_not_rhs_record():
    source='void f(App *p, Saved *s){\np->a=s->old_a;\np->b=s->old_b;\n}'
    header='typedef struct {int old_a,old_b;} Saved; typedef struct {int first;int a;int b;} App;'
    rows=[('i','sw $t0, 8($a0)','sw $t0, 4($a0)'),('i','sw $t1, 12($a0)','sw $t1, 8($a0)')]
    out=repairs.variants(source,header,'mips',rows,mapped(rows,[2,3]))
    assert len(out)==1 and 'int first; unsigned char cpu_layout_gap[4]; int a;' in out[0][2]
    assert 'typedef struct {int old_a,old_b;} Saved;' in out[0][2]


def test_auto_bundle_claim_race_does_not_publish(monkeypatch):
    net,proj,fn=prepared_net();net.is_rom=lambda p:True;net.fork_slug=lambda s:None
    net._function=lambda *a:net.store.one('SELECT * FROM functions WHERE id=1')
    net.project_row=lambda s:net.store.one('SELECT * FROM projects WHERE id=1')
    def compile(*args):
        with net.store.tx() as db:db.execute('DELETE FROM claims')
        return SimpleNamespace(ok=True,obj=b'object',effective_settings={})
    net._compile=compile;net._evaluate=lambda *a:('candidate',True,[])
    monkeypatch.setattr('decompnet.rom.export._single_definition',lambda *a:True)
    with pytest.raises(NetworkError,match='claim'):
        bundles.promote_measured(net,proj,fn,'new','context',{'toolchain':'ido-5.3','flags':['-O3']},
                                 Agent(1,'autopilot',None),prep.inputs(net,proj,fn))
    assert net.store.one('SELECT status FROM functions')['status']=='open'
    assert not net.store.one('SELECT 1 FROM attempts')


def test_macro_reached_application_type_can_reuse_verified_layout_without_shared_calls():
    source='void f(void){GetApp()->a=1;}'
    own='typedef struct {int a;} App; extern App app;\n#define GetApp() (&app)\n'
    donor='typedef struct {int gap; int a;} App; extern App app;\n#define GetApp() (&app)\n'
    assert 'App' in contexts.reachable_types(source,own)
    assert 'int gap;' in contexts.merge_records(source,own,donor)
    from decompnet.app import related_source_context as related
    class Store:
        def one(self,*a):return True
        def all(self,*a):return [{'name':'nearby','vram':4100,'asm':'lui $at, %hi(D_80004000)\njal different',
                                  'matched_source':'void nearby(void){}','verified_header':donor}]
    net=SimpleNamespace(store=Store(),is_rom=lambda p:True,fork_slug=lambda s:None)
    found=related.candidates(net,{'id':1,'slug':'g','arch':'mips'},
                             {'id':1,'vram':4096,'asm':'lui $at, %hi(D_80004000)\njal another'},source,own)
    assert len(found)==1 and 'int gap;' in found[0]['header']


def test_switch_clone_accepts_agreeing_donors_but_not_arbitrary_bodies():
    s='void f(int n){switch(n){case 0: show(D_80001000); mark(); break; case 1: show(D_80002000); mark(); break; default: fail(); break;}}'
    blocks={100:{'symbols':['D_80001000'],'calls':['show'],'signature':'same'},
            120:{'symbols':['D_80002000'],'calls':['show'],'signature':'same'},
            140:{'symbols':['D_80003000'],'calls':['show'],'signature':'same'},
            160:{'symbols':[],'calls':['fail'],'signature':'default'}}
    # A known macro is not mistaken for an extra target call.
    header='#define mark() done=1\n'
    out=switches.variants(s,'f',header,[(0,[100,120,140,160,160])],blocks)
    assert out and 'case 2:' in out[0][1] and 'show(D_80003000)' in out[0][1]
    assert 'case 3:' not in out[0][1]
    # A differing C-side effect is not arbitrarily chosen between donors.
    assert not any('case 2:' in x[1] for x in switches.variants(s.replace('case 1: show(D_80002000); mark();',
                     'case 1: show(D_80002000); mark(); extra=2;'), 'f',header,[(0,[100,120,140,160,160])],blocks))


@pytest.mark.parametrize('big',[True,False])
def test_target_table_reads_minus_one_lower_bound_and_both_byte_orders(big):
    from decompnet.app import switch_tables
    asm='addiu $v0, $a0, 1\nsltiu $at, $v0, 3\nlui $at, %hi(D_80004000)'
    data=b''.join(x.to_bytes(4,'big' if big else 'little') for x in (0x80001000,0x80001010,0x80001020))
    net=SimpleNamespace(is_rom=lambda p:True,symbols=lambda p:SimpleNamespace(resolve=lambda n:0x80004000),
                        reader=lambda *a:lambda address,size:data)
    got=switch_tables.read(net,{'id':1,'arch':'mips','big_endian':big},
                           {'asm':asm,'vram':0x80001000,'size':0x40})
    assert got==[(-1,[0x80001000,0x80001010,0x80001020])]


def test_reference_records_prototypes_and_call_form_can_be_tried_together():
    source='void f(Item *p){fetch(p);}'
    header='typedef struct {float x;} Vec;\nfloat fetch(int);\n#define fetch(p) ((p)->x)\n'
    reference='typedef struct {float x,y,z;} Vec;\nVec fetch(Item *p);'
    out=contexts.variants(source,header,reference,['fetch'])
    assert any('float x,y,z;' in h and 'Vec fetch(Item *p);' in h and '#define fetch' not in h for _,_,h in out)
    assert not contexts.declarations('void g(void){return fetch(1);}',{'fetch'})


def test_auto_promotion_requires_an_explicit_boolean_setting():
    net,proj,fn=prepared_net()
    with pytest.raises(NetworkError,match='checkbox'):
        prep.configure(net,proj,{'auto_exact':'false'})
    assert prep.configure(net,proj,{'enabled':False,'auto_exact':True})['auto_exact'] is True
