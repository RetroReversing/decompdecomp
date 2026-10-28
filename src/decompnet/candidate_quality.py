"""Retain agent-reported concerns and compiled alternatives without changing the judge or best C."""
import hashlib
import json
import re
import time

CONCERN=re.compile(r'uninitialized|undefined behavio[u]?r|semantically (?:incorrect|invalid)|invalid historical|historical[^.\n]{0,100}(?:invalid|incorrect)',re.I)
PREFIX='candidate-quality:'


def records(net,fn):
    row=net.store.one('SELECT value FROM settings WHERE key=?',PREFIX+str(fn['id']))
    return json.loads(row['value']) if row else []


def capture(net,fn,agent,notes,progress):
    if not CONCERN.search(notes) or fn['status']=='matched':
        return
    current=net.store.one('SELECT * FROM functions WHERE id=?',fn['id'])
    alternatives=net.store.all('SELECT id,source,score FROM attempts WHERE function_id=? AND agent_id=? '
                               'AND created_at>=? AND compiled=1 AND score>0 AND source IS NOT NULL '
                               'ORDER BY score,id DESC LIMIT 30',fn['id'],agent.id,
                               progress['claimed_at'] if progress else time.time())
    seen={current['best_source']};ids=[]
    for row in alternatives:
        if row['source'] in seen:
            continue
        seen.add(row['source']);ids.append(row['id'])
        if len(ids)==3:
            break
    report={'source_hash':hashlib.sha256((current['best_source'] or '').encode()).hexdigest(),
            'warning':notes[:600], 'agent':agent.name, 'alternatives':ids}
    saved=records(net,fn)
    for previous in saved:
        if previous['source_hash']==report['source_hash']:
            report['alternatives']=list(dict.fromkeys(report['alternatives']+previous['alternatives']))[:3]
    saved=[r for r in saved if r['source_hash']!=report['source_hash']]
    saved.append(report)
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(PREFIX+str(fn['id']),json.dumps(saved[-4:])))


def brief(net,fn):
    if fn['status']=='matched':
        return []
    current=hashlib.sha256((fn['best_source'] or '').encode()).hexdigest()
    saved=[r for r in records(net,fn) if r['source_hash']==current]
    if saved:
        return ['### Candidate quality (agent report, not proven)',saved[-1]['warning'][:300],
                'Compiled alternatives and evidence: get_function(section="quality"). Rejudge before using; '
                'a lower score alone does not establish valid C.']
    # Older notes remain discoverable; do not falsely anchor them to today's source.
    notes=net.store.all('SELECT text FROM notes WHERE function_id=? ORDER BY id DESC LIMIT 20',fn['id'])
    if any(CONCERN.search(r['text'] or '') for r in notes):
        return ['### Candidate quality (historical, unanchored report)',
                'An earlier agent reported a candidate-quality concern. Check get_function(section="quality"); '
                'the warning may concern older C.']
    return []


def view(net,fn):
    out=['Agent concerns are hypotheses, not semantic proofs. Alternatives retain their recorded scores; '
         'compile and judge them again in the current context. Best C and exact-match status are unchanged.']
    for report in records(net,fn)[-4:]:
        out += [report['agent']+': '+report['warning']]
        for ident in report['alternatives']:
            row=net.store.one('SELECT source,score FROM attempts WHERE id=? AND function_id=?',ident,fn['id'])
            if row:
                out += [f'Compiled alternative attempt {ident}, historical score {row["score"]} (quality unverified):',
                        '```c\n'+row['source'][:6000]+'\n```']
            if sum(map(len,out))>18000:
                return '\n\n'.join(out)+'\nFurther alternatives omitted.'
    if len(out)==1:
        notes=[r for r in net.store.all('SELECT * FROM notes WHERE function_id=? ORDER BY id DESC LIMIT 20',fn['id'])
               if CONCERN.search(r['text'] or '')][:2]
        for note in notes:
            out += ['Historical unanchored report: '+note['text'][:600]]
            rows=net.store.all('SELECT id,source,score FROM attempts WHERE function_id=? AND agent_id=? '
                               'AND created_at<=? AND compiled=1 AND score>0 AND source!=? ORDER BY id DESC LIMIT 3',
                               fn['id'],note['agent_id'],note['created_at'],fn['best_source'] or '')
            for row in rows:
                out += [f'Historical compiled alternative {row["id"]}, score {row["score"]}; quality unverified:',
                        '```c\n'+row['source'][:3000]+'\n```']
    return '\n\n'.join(out)
