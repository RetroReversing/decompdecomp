import json

from decompnet import match_bundle
from tests.test_claims import _game


def setup_bundle(tmp_path):
    tmp_path.mkdir(parents=True,exist_ok=True)
    net,_=_game(tmp_path,[('f',10,'open')]);net.on_match=lambda *a:None
    with net.store.tx() as db:
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':'rom','compiler':{'toolchain':'example','flags':[]}}),))
        db.execute("INSERT INTO context_versions(project_id,version,header,unit) VALUES(1,1,'typedef int s32;','')")
    agent,_=net.local_agent('test');fn=net._function('f','game');source='s32 f(void){return 0;}'
    net._record(fn,agent,0,source,1,matched=True)
    return net,source


def test_exact_record_retains_source_header_profile_target_and_portable_snapshots(tmp_path):
    net,source=setup_bundle(tmp_path);fn=net._function('f','game');bundle=match_bundle.status(net,fn)
    assert bundle['verdict']=='exact' and bundle['source']==source and bundle['header']=='typedef int s32;'
    assert bundle['changed_inputs']==[] and bundle['compiler']['toolchain']=='example'
    portable=match_bundle.portable(net,fn['project_id']);record=portable['matches']['f']
    assert portable['sources'][record['source_sha256']]==source
    assert portable['headers'][record['header_sha256']]=='typedef int s32;'
    assert 'target' not in record  # fingerprints, never ROM bytes


def test_later_source_header_flags_and_target_changes_are_explained_without_losing_evidence(tmp_path):
    net,source=setup_bundle(tmp_path)
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open',best_source='new draft',target=?",(b'changed',))
        db.execute("UPDATE context_versions SET header='typedef long s32;'")
        db.execute('UPDATE projects SET config=?',(json.dumps({'kind':'rom','compiler':{'toolchain':'example','flags':['-O1']}}),))
    bundle=match_bundle.status(net,net._function('f','game'))
    assert {'source changed','header changed','compiler settings changed','target/address changed'} <= set(bundle['changed_inputs'])
    assert bundle['source']==source and bundle['header']=='typedef int s32;'
    assert net._function('f','game')['status']=='open'  # evidence is not progress or an acceptance shortcut


def test_nonmatching_attempts_never_create_an_exact_bundle(tmp_path):
    net,_=_game(tmp_path,[('f',10,'open')]);agent,_=net.local_agent('test')
    net._record(net._function('f','game'),agent,10,'int f(void){return 1;}',1)
    assert match_bundle.status(net,net._function('f','game')) is None


def test_portable_history_survives_restore_but_cannot_accept_code_or_apply_flags(tmp_path):
    net,source=setup_bundle(tmp_path/'original');fn=net._function('f','game')
    manifest={'verification':match_bundle.portable(net,fn['project_id'])}
    (tmp_path/'restored').mkdir()
    other,_=_game(tmp_path/'restored',[('f',10,'open')]);target=other._function('f','game')
    match_bundle.restore_evidence(other,target['project_id'],'f','f',manifest)
    saved=match_bundle.portable(other,target['project_id'])
    assert saved['sources'][match_bundle.digest(source)]==source
    assert other._function('f','game')['status']=='open'
    assert other._function('f','game')['best_source'] is None
    # Invalid checksums never create a historical record.
    with other.store.tx() as db:db.execute('DELETE FROM verified_matches')
    manifest['verification']['sources'][match_bundle.digest(source)]='tampered'
    match_bundle.restore_evidence(other,target['project_id'],'f','f',manifest)
    assert not other.store.one('SELECT 1 FROM verified_matches')


def test_only_failed_rechecks_of_the_exact_source_raise_deduplicated_regression_alerts(tmp_path):
    net,source=setup_bundle(tmp_path);agent,_=net.local_agent('test');fn=net._function('f','game')
    net._record(fn,agent,20,'different draft',1)
    assert match_bundle.status(net,fn)['regression'] is None
    with net.store.tx() as db:
        db.execute("UPDATE functions SET status='open'")
        db.execute("UPDATE context_versions SET header='typedef long s32;'")
    fn=net._function('f','game')
    net._record(fn,agent,20,source,1)
    net._record(fn,agent,None,source,1)
    report=match_bundle.status(net,net._function('f','game'))
    assert report['regression']['score'] is None and 'header changed' in report['changed_inputs']
    assert net.store.one("SELECT COUNT(*) n FROM events WHERE kind='regression'")['n']==1
    net._record(net._function('f','game'),agent,0,source,1,matched=True)
    assert match_bundle.status(net,net._function('f','game'))['regression'] is None
