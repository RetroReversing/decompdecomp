"""Comparison checkpoints copy only local data; providers and compiler judges remain unchanged."""
import io
import json
import signal
import sqlite3
import time
from types import SimpleNamespace

import pytest

from decompnet.app import benchmark_worker, benchmarks
from decompnet.app.agents import SCHEMA, Manager
from decompnet.app.engine import RomNetwork
from decompnet.network import NetworkError


@pytest.fixture
def comparison(tmp_path,monkeypatch):
    monkeypatch.setenv('DECOMP_DETECT_AGENTS','off')
    project=tmp_path/'game-files'
    project.mkdir()
    (project/'source.c').write_text('int demo(void) { return 1; }\n')
    (project/'Makefile').write_text(f'ROOT := {project}\n')
    state=tmp_path/'app'
    state.mkdir()
    net=RomNetwork(state/'state.db',tmp_path)
    with net.store.tx() as db:
        for slug in ('demo','other'):
            pid=db.execute('INSERT INTO projects(slug,name,arch,root,config) VALUES(?,?,?,?,?)',
                           (slug,slug,'mips',str(project),json.dumps({'kind':'rom'}))).lastrowid
            for name in ('one','two'):
                db.execute('INSERT INTO functions(project_id,name,n_insns,size,best_score) VALUES(?,?,10,40,500)',(pid,name))
        for command in SCHEMA: db.execute(command)
    app=SimpleNamespace(home=state,net=net,manager=SimpleNamespace(sessions={}),settings=SimpleNamespace(endpoint=lambda x:None))
    service=benchmarks.Benchmarks(app)
    return service,app,project


def test_checkpoint_is_project_scoped_immutable_and_never_starts_workers(comparison):
    service,app,project=comparison
    source_before=benchmarks.files(project)
    cp=service.checkpoint('demo',log=lambda x:None)
    folder=service.folder(cp['id'])
    db=sqlite3.connect(folder/'checkpoint.db')
    assert db.execute('SELECT slug FROM projects').fetchall()==[('demo',)]
    assert db.execute('SELECT COUNT(*) FROM functions').fetchone()[0]==2
    assert db.execute('SELECT COUNT(*) FROM helpers').fetchone()[0]==0
    assert db.execute('SELECT root FROM projects').fetchone()[0]==str(folder/'project')
    db.close()
    assert benchmarks.files(project)==source_before
    assert str(folder/'project') in (folder/'project/Makefile').read_text()
    assert service.inventory('demo')[0]['id']==cp['id']
    assert not service.active and not (folder/'runs').exists()
    with pytest.raises(NetworkError): service.folder('../outside')


def test_checkpoint_rejects_active_agents_or_files_changing_during_copy(comparison,monkeypatch):
    service,app,project=comparison
    app.manager.sessions={'x':SimpleNamespace(slug='demo',state='working')}
    with pytest.raises(NetworkError,match='Stop'): service.checkpoint('demo')
    app.manager.sessions={}
    copy=benchmarks.shutil.copytree
    def changing(*a,**kw):
        out=copy(*a,**kw)
        (project/'source.c').write_text('changed while copying')
        return out
    monkeypatch.setattr(benchmarks.shutil,'copytree',changing)
    with pytest.raises(NetworkError,match='changed while copying'): service.checkpoint('demo',log=lambda x:None)
    assert not list(service.root.glob('*/checkpoint.json'))


def test_trials_receive_identical_copies_equal_limits_and_credentials_only_on_stdin(comparison,monkeypatch):
    service,app,project=comparison
    cp=service.checkpoint('demo',log=lambda x:None)
    app.settings.endpoint=lambda x:{'id':'endpoint','name':'Local test','provider':'custom','base_url':'http://localhost:1/v1','api_key':'private-secret','model':'m'}
    requests=[]
    class Input(io.StringIO):
        def close(self):
            request=json.loads(self.getvalue())
            requests.append(request)
            super().close()
    class Process:
        def __init__(self,argv,**kw):
            assert 'private-secret' not in repr(argv)
            self.folder=__import__('pathlib').Path(argv[-1])
            self.stdin=Input()
            db=sqlite3.connect(self.folder/'trial.db')
            assert db.execute('SELECT COUNT(*) FROM functions').fetchone()[0]==2
            root=db.execute('SELECT root FROM projects').fetchone()[0]
            db.close()
            assert root==str(self.folder/'project')
            assert (self.folder/'project/source.c').read_text()==(project/'source.c').read_text()
            index=len(requests)
            ids=['1','2'] if index==0 else ['1']
            names=['shared-original','exclusive'] if index==0 else ['shared-renamed']
            benchmarks.write(self.folder/'result.json',{'state':'complete','matches':len(ids),'improved':len(ids),'matched_ids':ids,'matched_functions':names})
        def poll(self): return 0
    monkeypatch.setattr(benchmarks.subprocess,'Popen',Process)
    monkeypatch.setattr(benchmarks.limits,'capped',lambda x:x)
    report=service.run(cp['id'],[{'kind':'api','model':'model-a','endpoint':'endpoint'},
                                 {'kind':'api','model':'model-b','endpoint':'endpoint'}],{'seconds':30,'tokens':1000},{},log=lambda x:None)
    assert report['state']=='complete'
    assert [r['options']['limits'] for r in requests]==[{'seconds':30,'tokens':1000}]*2
    assert requests[0]['baseline']==requests[1]['baseline']
    assert requests[0]['options']['api_key']=='private-secret'
    assert report['trials'][0]['unique_matches']==['exclusive']
    assert report['trials'][1]['unique_matches']==[]
    assert 'private-secret' not in (service.folder(cp['id'])/'report.json').read_text()
    assert app.net.store.one('SELECT COUNT(*) AS n FROM functions')['n']==4
    assert not service.active


def test_changed_checkpoint_or_missing_budget_prevents_trial_start(comparison):
    service,app,project=comparison
    cp=service.checkpoint('demo',log=lambda x:None)
    plans=[{'kind':'codex','model':'one'},{'kind':'codex','model':'two'}]
    with pytest.raises(NetworkError,match='budget'): service.run(cp['id'],plans,{}, {},log=lambda x:None)
    (service.folder(cp['id'])/'project/source.c').write_text('tampered')
    with pytest.raises(NetworkError,match='changed'): service.run(cp['id'],plans,{'seconds':10},{},log=lambda x:None)


def test_worker_uses_private_engine_and_reports_deltas_without_paid_calls(comparison,monkeypatch):
    service,app,project=comparison
    cp=service.checkpoint('demo',log=lambda x:None)
    checkpoint=benchmarks.read(service.folder(cp['id'])/'checkpoint.json')
    trial=service.root/'test-trial'
    trial.mkdir()
    benchmarks.shutil.copytree(service.folder(cp['id'])/'project',trial/'project')
    benchmarks.shutil.copyfile(service.folder(cp['id'])/'checkpoint.db',trial/'trial.db')
    benchmarks.relocate(trial/'trial.db',service.folder(cp['id'])/'project',trial/'project')
    def fake_run(manager,s):
        assert manager.net is not app.net and manager.net.project_row('demo')['root']==str(trial/'project')
        agent,_=manager.net.local_agent(s.agent_name)
        row=manager._session_begin(s)
        with manager.net.store.tx() as db:
            db.execute("UPDATE functions SET status='matched',best_score=0,matched_agent_id=?,matched_at=? WHERE id=1",(agent.id,time.time()))
            db.execute('INSERT INTO attempts(function_id,agent_id,score,compiled,created_at) VALUES(1,?,0,1,?)',(agent.id,time.time()))
        manager.account(s,{'input':100,'output':10})
        manager._session_end(s,row,agent,time.time(),'done')
        manager._finish(s,'test completed')
    monkeypatch.setattr(Manager,'_run',fake_run)
    # Avoid modifying pytest's own signal handlers when exercising the worker in-process.
    original={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        benchmark_worker.run(trial,{'slug':'demo','kind':'codex','options':{'model':'known','limits':{'seconds':5}},'baseline':checkpoint['baseline']})
    finally:
        for sig,handler in original.items():signal.signal(sig,handler)
    result=benchmarks.read(trial/'result.json')
    assert result['state']=='complete' and result['matches']==1 and result['improved']==1
    assert result['verified_bytes']==40 and result['usage']['tokens']==110
    assert app.net.store.one('SELECT status FROM functions WHERE id=1')['status']=='open'
    assert (project/'source.c').read_text()=='int demo(void) { return 1; }\n'


def test_checkpoint_cleanup_and_shutdown_cancel_only_owned_trials(comparison):
    service,app,project=comparison
    stop=__import__('threading').Event()
    trial=service.root/'owned'
    trial.mkdir()
    service.active['owned']={'stop':stop,'folder':trial,'process':None}
    service.shutdown()
    assert stop.is_set() and (trial/'stop').exists()
    assert (project/'source.c').exists()


def test_imported_checkpoint_requires_explicit_build_permission(comparison):
    service,app,project=comparison
    app.net._update_cfg('demo',lambda cfg:cfg.update(kind='fork'))
    cp=service.checkpoint('demo',log=lambda x:None)
    with pytest.raises(NetworkError,match='build scripts'):
        service.run(cp['id'],[{'kind':'codex','model':'a'},{'kind':'codex','model':'b'}],{'seconds':1},{},log=lambda x:None)


def test_cancel_after_copy_never_starts_a_provider(comparison,monkeypatch):
    service,app,project=comparison
    cp=service.checkpoint('demo',log=lambda x:None)
    real=benchmarks.shutil.copytree
    def copying(*a,**kw):
        out=real(*a,**kw)
        service.stop(cp['id'])
        return out
    monkeypatch.setattr(benchmarks.shutil,'copytree',copying)
    monkeypatch.setattr(benchmarks.subprocess,'Popen',lambda *a,**kw:pytest.fail('No provider process after cancellation'))
    report=service.run(cp['id'],[{'kind':'codex','model':'a'},{'kind':'codex','model':'b'}],{'seconds':1},{},log=lambda x:None)
    assert report['state']=='cancelled' and report['trials']==[]
    assert service.inventory('demo')[0]['reports'][0]['state']=='cancelled'


def test_worker_summary_excludes_changed_targets_and_flags_regressions(comparison):
    service,app,project=comparison
    cp=service.checkpoint('demo',log=lambda x:None)
    baseline=benchmarks.read(service.folder(cp['id'])/'checkpoint.json')['baseline']
    baseline['1']['matched']=True
    manager=SimpleNamespace(sessions={},snapshot=lambda:[])
    session=SimpleNamespace(id=100,why='test')
    with app.net.store.tx() as db:
        db.execute("UPDATE functions SET target=x'01020304',status='matched',best_score=0 WHERE id=2")
    r=benchmark_worker.result(app.net,'demo',baseline,session,manager,time.time())
    assert r['matches']==0 and r['changed_targets']==['two'] and r['regressions']==['one']
    assert r['state']=='failed'


def test_trial_can_recover_a_real_byte_verified_function_without_touching_original(comparison,monkeypatch):
    from decompnet import toolchains
    if toolchains.installed('gcc-2.7.2-kmc') is None or toolchains.installed('binutils-mips') is None:
        pytest.skip('needs installed pinned free MIPS compiler and assembler')
    service,app,project=comparison
    target=bytes.fromhex('03e0000800851021')
    (project/'program.bin').write_bytes(target)
    cfg={'kind':'rom','platform':'n64','rom':{'file':'program.bin'},'segments':[], 'big_endian':True,
         'compiler':{'toolchain':'gcc-2.7.2-kmc','flags':['-O2','-G0','-mips3','-mgp32','-mfp32','-mno-abicalls','-fno-pic']}}
    with app.net.store.tx() as db:
        db.execute('UPDATE projects SET config=?,big_endian=1 WHERE slug=?',(json.dumps(cfg),'demo'))
        db.execute("UPDATE functions SET target=?,relocs='[]',vram=2147487744,rom_offset=0,size=8,n_insns=2,best_score=50,asm='jr $ra\naddu $v0,$a0,$a1' WHERE id=1",(target,))
        db.execute("INSERT INTO context_versions(project_id,version,header,created_at) VALUES(1,1,'',0)")
    cp=service.checkpoint('demo',log=lambda x:None)
    checkpoint=benchmarks.read(service.folder(cp['id'])/'checkpoint.json')
    trial=service.root/'judge-trial'
    trial.mkdir()
    benchmarks.shutil.copytree(service.folder(cp['id'])/'project',trial/'project')
    benchmarks.shutil.copyfile(service.folder(cp['id'])/'checkpoint.db',trial/'trial.db')
    benchmarks.relocate(trial/'trial.db',service.folder(cp['id'])/'project',trial/'project')
    def provider(manager,s):
        agent,_=manager.net.local_agent(s.agent_name)
        row=manager._session_begin(s)
        manager.net.claim_row(agent,'demo',function='one')
        verdict=manager.net.compile_and_diff(agent,'one','int one(int a,int b) { return a+b; }','demo')
        assert manager.net._function('one','demo')['status']=='matched',verdict
        manager.account(s,{'input':100,'output':10})
        manager._session_end(s,row,agent,time.time(),'done')
        manager._finish(s,'deterministic provider finished')
    monkeypatch.setattr(Manager,'_run',provider)
    original={sig:signal.getsignal(sig) for sig in (signal.SIGINT,signal.SIGTERM)}
    try:
        benchmark_worker.run(trial,{'slug':'demo','kind':'codex','options':{'model':'known','limits':{'seconds':30}},'baseline':checkpoint['baseline']})
    finally:
        for sig,handler in original.items():signal.signal(sig,handler)
    r=benchmarks.read(trial/'result.json')
    assert r['matches']==1 and r['verified_bytes']==8 and r['usage']['tokens']==110
    assert app.net._function('one','demo')['status']=='open'


def test_relocation_handles_json_windows_separators_and_external_sibling_paths(tmp_path):
    from pathlib import PureWindowsPath
    before=PureWindowsPath('C:/Old Project')
    after=PureWindowsPath('D:/Trial Project')
    assert benchmarks.remap({'path':r'C:\Old Project\obj\x.o'},before,after)=={'path':r'D:\Trial Project\obj\x.o'}
    assert benchmarks.remap('C:/Old Project/obj/x.o',before,after)=='D:/Trial Project/obj/x.o'
    assert benchmarks.remap('C:/Old Project-tools/x',before,after)=='C:/Old Project-tools/x'
    root=tmp_path/'copy'
    root.mkdir()
    old=tmp_path/'old project'
    (root/'objdiff.json').write_text(json.dumps({'target':str(old/'build/object.o')}))
    db=tmp_path/'relocate.db'
    c=sqlite3.connect(db)
    c.execute('CREATE TABLE projects(id INTEGER,root TEXT,config TEXT)')
    c.execute('INSERT INTO projects VALUES(1,?,?)',(str(old),json.dumps({'path':str(old/'rom.bin')})))
    c.commit();c.close()
    benchmarks.relocate(db,old,root)
    assert json.loads((root/'objdiff.json').read_text())['target']==str(root/'build/object.o')


@pytest.mark.parametrize('selection',[{'strategy':'random'},{'model_untried':True}])
def test_comparisons_require_a_common_deterministic_queue(comparison,selection):
    service,app,project=comparison
    cp=service.checkpoint('demo',log=lambda x:None)
    with pytest.raises(NetworkError,match='common queue'):
        service.run(cp['id'],[{'kind':'codex','model':'a'},{'kind':'codex','model':'b'}],{'seconds':1},selection,log=lambda x:None)
