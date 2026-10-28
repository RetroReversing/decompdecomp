"""Read-only queue explanations and per-function model evidence, using the actual scheduler."""
import collections
import json
import time

from .. import claim_stalls, network


def helper(manager, sid):
    s = manager.sessions.get(str(sid))
    if s is None:
        raise network.NetworkError('This agent no longer exists. Refresh the list.')
    return s


def preview(manager, sid, limit=20):
    s = helper(manager,sid)
    net = manager.net
    proj = net.project_row(s.slug)
    row = net.store.one('SELECT * FROM agents WHERE name=?',s.agent_name)
    agent = network.Agent(row['id'] if row else -1,s.agent_name,None,True)
    now = time.time()
    sdk, blocked = net.sdk_functions(proj), claim_stalls.blocked(net,proj)
    with net.store.tx() as db:  # read only; no claim, release, expiry or random model call
        query,args,route = net.queue_query(db,proj,agent,'easiest',now,sdk,blocked,1000)
        eligible = list(db.execute(query,args))
        if not eligible and route == 'weak':
            eligible = list(db.execute(query,args | {'weak':False}))
        claims = {r['function_id']:r['agent_id'] for r in db.execute('SELECT * FROM claims WHERE expires_at>?',(now,))}
        tries = {r['function_id']:r['n'] for r in db.execute('SELECT function_id,COUNT(*) AS n FROM attempts WHERE agent_id=? GROUP BY function_id',(agent.id,))}
        recent = {r['detail'] for r in db.execute("SELECT detail FROM events WHERE project_id=? AND agent_id=? AND kind='release' AND created_at>?",(proj['id'],agent.id,args['mine']))}
    priority=net.queue_priority(s.slug,agent) if hasattr(net,'queue_priority') else None
    if priority is not None:
        eligible=[priority]+[r for r in eligible if r['id']!=priority['id']]
    policy = net.selection(s.slug,s.agent_name)
    model_tried = set(json.loads(args['model_tried']))
    ids = {r['id'] for r in eligible}
    reasons, excluded = collections.Counter(), []
    for fn in net.store.all('SELECT * FROM functions WHERE project_id=?',proj['id']):
        why = []
        if fn['status'] != 'open': why.append('matched' if fn['status']=='matched' else 'set aside')
        if fn['id'] in claims: why.append('currently claimed')
        if fn['id'] in blocked: why.append('unchanged candidate stalled')
        if fn['id'] in model_tried: why.append('already checked by this model')
        if policy.get('max_tries') and tries.get(fn['id'],0)>=policy['max_tries']: why.append('worker attempt limit')
        if fn['best_score'] is not None and policy.get('match_below') is not None:
            if 100*(1-fn['best_score']/max(1,(fn['n_insns'] or 0)*100))>=policy['match_below']: why.append('outside match filter')
        if fn['name'] in recent: why.append('recently released by this worker')
        if priority is not None and fn['id']==priority['id']: why=[]
        if not why and fn['id'] not in ids: why.append('adaptive routing or outside preview window')
        if why:
            reasons.update(why)
            if len(excluded)<100: excluded.append({'name':fn['name'],'reasons':why})
    current = net.store.one('SELECT f.name FROM claims c JOIN functions f ON f.id=c.function_id WHERE c.agent_id=? AND c.expires_at>?',agent.id,now)
    return {'current':current['name'] if current else None,'strategy':policy.get('strategy') or 'automatic',
            'next':[{'name':r['name'],'instructions':r['n_insns'],'sdk':r['id'] in sdk} for r in eligible[:max(1,min(100,int(limit)))]],
            'eligible_in_preview':len(eligible),'excluded_counts':dict(reasons),'excluded':excluded,
            'note':'A snapshot of up to 1,000 eligible functions; live claims and automatic routing can change the order. SDK functions are deferred in automatic mode, not excluded.'}


def models(manager, slug, name):
    net=manager.net
    fn=net._function(name,slug)
    rows=net.store.all(
        'WITH history AS (SELECT t.*,MIN(CASE WHEN t.compiled THEN t.score END) OVER '
        '(ORDER BY t.id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous '
        'FROM attempts t WHERE t.function_id=?) '
        'SELECT h.*,a.name AS agent,s.kind,s.model FROM history h JOIN agents a ON a.id=h.agent_id '
        'LEFT JOIN helper_sessions s ON s.id=(SELECT ss.id FROM helper_sessions ss WHERE ss.project=? '
        'AND ss.agent=a.name AND ss.started_at<=h.created_at AND (ss.ended_at IS NULL OR ss.ended_at>=h.created_at) '
        'ORDER BY ss.started_at DESC,ss.id DESC LIMIT 1) ORDER BY h.id',fn['id'],slug)
    board={}
    for r in rows:
        key=(r['kind'] or 'unattributed',r['model'] or 'Unknown / local tools')
        cell=board.setdefault(key,{'kind':key[0],'model':key[1],'checks':0,'improvements':0,'best_percent':None,'verified':False})
        cell['checks']+=1
        if r['compiled'] and r['score'] is not None and r['score']>=0:
            percent=max(0,min(99.9,100*(1-r['score']/max(1,(fn['n_insns'] or 0)*100)))) if r['score'] else 100
            cell['best_percent']=max(cell['best_percent'] or 0,percent)
            cell['improvements']+=int(r['score']<(r['previous'] if r['previous'] is not None else (fn['n_insns'] or 0)*100))
            cell['verified'] |= r['score']==0 and fn['status']=='matched'
    costs={(r['kind'],r['model']):r for r in manager.costs(slug)}
    for key,cell in board.items():
        spend=costs.get(key,{})
        cell['project_tokens']=spend.get('tokens') or None
        cell['project_usd']=spend.get('usd') or None
    return {'models':sorted(board.values(),key=lambda r:-(r['best_percent'] or 0)),
            'note':'Checks and percentages are for this function. Tokens and reported dollars are lifetime totals for this model on the project, not costs allocated to this function. Unknown attribution is kept separate.'}


def diagnosis(diff):
    labels={'r':('Register allocation','Try local declaration order, types and expression shape.'),
            's':('Stack layout','Check local variable types, address-taking and declaration order.'),
            'o':('Instruction order','Check expression order and the selected compiler/assembler flags.'),
            'i':('Operands or constants','Check literal types, signedness, field offsets and relocations.'),
            '|':('Instruction choices','Check control flow, types and compiler settings.'),
            '<':('Missing instructions','Check omitted side effects and control-flow paths.'),
            '>':('Extra instructions','Check redundant work, promotions and compiler optimization.')}
    groups={}
    for i,row in enumerate(diff.get('rows') or []):
        if row[0] not in labels: continue
        label,hint=labels[row[0]]
        cell=groups.setdefault(row[0],{'marker':row[0],'title':label,'hint':hint,'count':0,'rows':[]})
        cell['count']+=1
        if len(cell['rows'])<8: cell['rows'].append(i)
    return {'groups':sorted(groups.values(),key=lambda r:-r['count']),
            'note':'Observed differences, not a proven cause. Compiler, assembler and source changes can produce similar symptoms. The exact-byte judge is unchanged.',
            'error':diff.get('error')}
