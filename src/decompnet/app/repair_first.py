"""Try a small deterministic pass once per source/build scope before handwritten batches."""
from ..network import NetworkError
from . import agent_targets, agent_tokens, local_experiments


def before_variants(net, agent, slug, args):
    fn=net._function(args['function'],slug)
    with net.attempt_turn(agent,fn):
        source,revision=agent_tokens.candidate(net,agent,slug,fn['name'])
        if revision != args.get('revision'):
            raise agent_tokens.stale(net,agent,fn,args.get('revision'),source,revision)
        patches=args.get('patches')
        if not isinstance(patches,list) or not 1<=len(patches)<=6 or any(not isinstance(p,dict) for p in patches):
            raise NetworkError('Send 1 to 6 independent patches with old and new text.')
        for patch in patches:
            agent_targets.replace_anchor(source,patch.get('old'),patch.get('new'),patch.get('occurrence',0))
        scope=agent_tokens.target_scope(net,fn,revision)
        key='repair-first:'+str(fn['id'])+':'+scope
        if net.store.one('SELECT 1 FROM settings WHERE key=?',key):
            return None
        # Existing target/compiled ledgers prevent repeated failed experiments. Persist
        # this entry across sessions too, including an exhausted/no-applicable pass.
        result=local_experiments.run(net,agent,slug,fn['name'],revision,max_trials=3,seconds=15)
        with net.store.tx() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,'completed'))
        if result.startswith('No untried applicable'):
            return None
        return ('Repair-first pass (up to 3 compiles; requested handwritten variants were not submitted):\n'+result+
                '\nUse the returned continuation revision. If still needed, submit your independent patches against that revision.')
