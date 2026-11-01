import json

from decompnet.app import random_safety
from decompnet.app.engine import RomNetwork
from decompnet.candidate_quality import view


def test_nested_compound_mutation_is_rejected_but_independent_updates_are_not():
    original='void f(void){a+=n;b+=n;c+=n;d+=n;}'
    harmful='void f(void){a+=(b+=(c+=(d+=n)));}'
    assert random_safety.unsafe_update(original,harmful)
    assert not random_safety.unsafe_update(original,original.replace('b+=n;c+=n;','c+=n;b+=n;'))
    assert not random_safety.unsafe_update(original,'void f(void){a=b=c=0;}')
    assert not random_safety.unsafe_update(harmful,harmful)
    assert not random_safety.unsafe_update(original,'void f(void){puts("a+=(b+=n)");}')


def test_first_search_checkpoint_survives_better_scores_and_restart(tmp_path):
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    fn={'id':17,'best_source':'initial C','best_score':90}
    random_safety.checkpoint(net,fn)
    for n in range(5):
        random_safety.checkpoint(net,dict(fn,best_source='exploratory '+str(n),best_score=80-n))
    net.store.conn.close()
    net=RomNetwork(tmp_path/'app.db',tmp_path)
    saved=json.loads(net.store.one('SELECT value FROM settings WHERE key=?','permuter-checkpoint:17')['value'])
    assert len(saved)==3 and saved[0]['source']=='initial C'
    assert saved[-1]['source']=='exploratory 4'
    assert all('unverified' in s['quality'] for s in saved)
    text=view(net,fn)
    assert 'initial C' in text and 'exploratory 4' in text
    assert 'does not prove functional correctness' in text
    assert net.store.one('SELECT COUNT(*) n FROM functions')['n']==0
