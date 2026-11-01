"""Keep a pre-random-search checkpoint and reject one known side-effecting mutation shape.

This is not a C equivalence checker. Near misses remain exploratory and only the normal
exact judge can accept a match. A checkpoint's functionality is explicitly unverified.
"""
import hashlib
import json
import re
import time

from ..forks.csource import _blank

PREFIX = 'permuter-checkpoint:'


def nested_updates(source):
    # Chaining formerly independent += updates nests side effects and changes the values
    # added to outer destinations. Count these expressions, not ordinary a=b=c=0 copies.
    return sum(len(re.findall(r'(?:<<|>>|[+*/%&|^-])=(?!=)', statement))>1
               for statement in _blank(source).split(';'))


def unsafe_update(parent, candidate):
    return nested_updates(candidate)>nested_updates(parent)


def checkpoint(net, fn):
    source=fn['best_source']
    if not source:
        return
    key=PREFIX+str(fn['id'])
    row=net.store.one('SELECT value FROM settings WHERE key=?',key)
    saved=json.loads(row['value']) if row else []
    digest=hashlib.sha256(source.encode()).hexdigest()
    if any(item['source_hash']==digest for item in saved):
        return
    item={'source_hash':digest,'source':source,'score':fn['best_score'],'saved_at':time.time(),
          'quality':'unverified; saved before random rewriting'}
    # Keep the first checkpoint as well as two recent alternatives, even across restarts.
    saved=(saved[:1]+saved[-1:]+[item]) if len(saved)>=3 else saved+[item]
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(saved)))


def view(net, fn):
    row=net.store.one('SELECT value FROM settings WHERE key=?',PREFIX+str(fn['id']))
    if not row:
        return []
    out=['### Before random search (quality unverified)',
         'These checkpoints are retained separately from the lowest-scoring exploratory draft. '
         'A lower score does not prove functional correctness; rejudge any restored source.']
    for item in json.loads(row['value']):
        out += [f"Checkpoint {item['source_hash'][:20]}, historical score {item['score']}:",
                '```c\n'+item['source'][:6000]+'\n```'+('\nPreview truncated; the full checkpoint is retained locally.' if len(item['source'])>6000 else '')]
    return out
