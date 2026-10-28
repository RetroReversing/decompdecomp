"""Bounded source/profile diagnostics from one revision; never apply compiler choices implicitly."""
import hashlib
import json
import re
import time

from .. import consoles
from ..network import NetworkError
from . import agent_targets, agent_tokens


def run(net,agent,slug,function,revision,trials):
    if not isinstance(trials,list) or not 1<=len(trials)<=6:
        raise NetworkError('Send 1–6 source/compiler trials.')
    fn=net._function(function,slug)
    with net.attempt_turn(agent,fn):
        source,actual=agent_tokens.candidate(net,agent,slug,function)
        if actual!=revision:
            raise agent_tokens.stale(net,agent,fn,revision,source,actual)
        proj=net._project(slug);cfg=net.cfg(proj);fork=bool(net.fork_slug(slug))
        planned=[]
        for t in trials:
            if not isinstance(t,dict) or not isinstance(t.get('compiler'),str) or not t['compiler'].strip() or t['compiler'].strip().lower()=='all':
                raise NetworkError('Each trial needs one explicit compiler; all is not a bounded profile.')
            if not isinstance(t.get('flags',''),str):
                raise NetworkError('flags must be a string.')
            text=agent_targets.replace_anchor(source,t['old'],t.get('new',''),t.get('occurrence',0)) if 'old' in t else source
            if not fork:
                compiler=net._toolchain_named(cfg,t['compiler'])
                flags=t.get('flags','').split() or consoles.default_flags(cfg,compiler)
                t={**t,'compiler':compiler,'flags':' '.join(flags)}
            planned.append((t,text))
        scope=hashlib.sha256(repr((agent_tokens.target_scope(net,fn,revision),dict(fn),
            [dict(r) for r in net.store.all('SELECT * FROM symbols WHERE project_id=? ORDER BY name',fn['project_id'])],
            net._generation.get(fn['project_id'],0))).encode()).hexdigest()
        began=time.monotonic();out=[];seen=set()
        ledger=getattr(net,'_compiler_trials',{})
        net._compiler_trials=ledger
        for i,(t,text) in enumerate(planned,1):
            if out and time.monotonic()-began>=60:
                out.append('60-second budget reached; remaining profiles untested.');break
            key=hashlib.sha256(repr((agent.id,slug,fn['id'],scope,text,t['compiler'],t.get('flags',''))).encode()).hexdigest()[:20]
            if key in seen:
                continue
            seen.add(key)
            cached=ledger.get(key) if not fork else None
            if cached:
                result=cached['result'];tag='cached diagnostic'
            else:
                result=net.try_compiler(agent,slug,function,t['compiler'],t.get('flags',''),text,keep_match=False)
                ledger[key]={'agent':agent.id,'slug':slug,'function':fn['name'],'source':text,'compiler':t['compiler'],
                             'flags':t.get('flags',''),'revision':revision,'scope':scope,'project_config':cfg,'flags_source':'project build owns per-file flags' if fork else 'explicit effective flags','result':result}
                while len(ledger)>64:
                    del ledger[next(iter(ledger))]
                tag='diagnostic'
            score=re.search(r'\bscore\s+(\d+)',result)
            exact='MATCHES the game byte for byte' in result or 'MATCH!' in result
            status='EXACT (diagnostic only)' if exact else 'score '+score[1] if score else result.splitlines()[0][:180]
            out.append(f'{i}: {status}; {tag}; trial_id={key}; compiler={t["compiler"]}; flags={t.get("flags","") or "platform defaults"}')
            if exact:
                break
        return '\n'.join(out)+f'\nCandidate revision {actual} unchanged. get_compiler_trial retrieves the exact C/profile pair. No match credited or compiler choice applied.'


def retrieve(net,agent,slug,function,trial_id):
    fn=net._function(function,slug);record=getattr(net,'_compiler_trials',{}).get(trial_id)
    if not record or (record['agent'],record['slug'],record['function'])!=(agent.id,slug,fn['name']):
        raise NetworkError('Trial is unavailable for this worker/function; rerun the bounded experiment.')
    return json.dumps(record,ensure_ascii=False)
