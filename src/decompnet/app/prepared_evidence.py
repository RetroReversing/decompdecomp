"""Private source/header/profile evidence: recompile first, publish one exact bundle atomically."""
import hashlib
import json

from ..network import NetworkError
from . import source_preparation as prep


def digest(net, proj, fn):
    from .permuter_inputs import build_state
    return hashlib.sha256(repr((prep.inputs(net,proj,fn),build_state(proj))).encode()).hexdigest()


def listing(net, proj):
    rows=net.store.all("SELECT r.signature,r.feedback,f.name,f.status FROM reuse_trials r JOIN functions f ON f.id=r.function_id WHERE f.project_id=? AND r.signature LIKE 'local-source-%' ORDER BY r.rowid DESC LIMIT 20",proj['id'])
    result=[]
    for row in rows:
        item=json.loads(row['feedback'] or '{}')
        if not item:continue
        result.append({'id':row['signature'],'function':row['name'],'score':item.get('score'),
                       'exact':bool(item.get('exact')),'flags':item.get('flags',[]),
                       'toolchain':item.get('toolchain'),'category':item.get('category','legacy'),
                       'source':item.get('source',''),'header':item.get('header',''),'diagnostic':item.get('diagnostic',''),
                       'reviewable':bool(item.get('exact') and item.get('inputs') and row['status']=='open')})
    return result


def scoped_config(proj, fn, settings):
    """Replace only this entry's address interval, keeping surrounding profile ranges."""
    cfg=json.loads(proj['config']);lo,hi=fn['vram'],fn['vram']+fn['size'];keep=[]
    for old in cfg.get('compiler_ranges',[]):
        for a,b in ((old['start'],min(old['end'],lo)),(max(old['start'],hi),old['end'])):
            if a<b:keep.append({**old,'start':a,'end':b})
    cfg['compiler_ranges']=sorted(keep+[{'start':lo,'end':hi,**settings}],key=lambda r:r['start'])
    return cfg


def publish_profile(net, db, fn, expected, settings, agent):
    """Inside _record's transaction. Compilation and the exact verdict already happened outside it."""
    proj=db.execute('SELECT * FROM projects WHERE id=?',(fn['project_id'],)).fetchone()
    claim=db.execute('SELECT agent_id,expires_at FROM claims WHERE function_id=?',(fn['id'],)).fetchone()
    import time
    if (fn['status']!='open' or prep.inputs(net,proj,fn)!=expected or not claim
            or claim['agent_id']!=agent.id or claim['expires_at']<=time.time()):
        raise NetworkError('The prepared bundle changed or its claim expired. Run preparation again.')
    if not fn['size'] or fn['size']<=0:
        raise NetworkError('The function needs a valid extent before applying a compiler bundle.')
    if db.execute('SELECT 1 FROM functions WHERE project_id=? AND id!=? AND vram<? AND vram+size>?',
                  (fn['project_id'],fn['id'],fn['vram']+fn['size'],fn['vram'])).fetchone():
        raise NetworkError('The compiler range overlaps another function. Resolve the boundary before applying this bundle.')
    cfg=scoped_config(proj,fn,settings)
    db.execute('UPDATE projects SET config=? WHERE id=?',(json.dumps(cfg),proj['id']))
    net._event(db,proj['id'],agent.id,'compiler','Approved source/header/profile bundle for '+fn['name'])
    return prep.inputs(net,{**dict(proj),'config':json.dumps(cfg)},fn)


def apply(net, proj, key, log):
    from ..rom.export import _single_definition
    if not net.is_rom(proj) or net.fork_slug(proj['slug']):
        raise NetworkError('Prepared profile bundles are available for local ROM projects; imported builds own their headers and compiler settings.')
    row=net.store.one('SELECT r.*,f.project_id FROM reuse_trials r JOIN functions f ON f.id=r.function_id WHERE r.signature=? AND f.project_id=?',key,proj['id'])
    if not row:raise NetworkError('This prepared bundle is no longer available. Run preparation again.')
    item=json.loads(row['feedback'] or '{}')
    fn=net.store.one('SELECT * FROM functions WHERE id=?',row['function_id'])
    if not item.get('exact') or item.get('inputs')!=digest(net,proj,fn) or fn['status']!='open':
        raise NetworkError('This prepared bundle is stale or not exact. Run preparation again.')
    agent,_=net.local_agent('autopilot')
    if not net.permuter._hold(agent,fn,count_turn=False,seconds=60):
        raise NetworkError('This function is being worked on. Wait until it is released.')
    try:
        expected=prep.inputs(net,proj,fn)
        settings={'toolchain':item['toolchain'],'flags':item['flags']}
        private={**dict(proj),'config':json.dumps(scoped_config(proj,fn,settings))}
        log('Rechecking prepared source, header, flags and ROM data…')
        result=net._compile(private,item['header'],item['source'],fn)
        if not result.ok or not _single_definition(result.obj,fn['name']):
            raise NetworkError('The prepared bundle no longer compiles as one function. Run preparation again.')
        candidate,verdict,problems=net._evaluate(fn,proj,result.obj)
        if candidate is None or verdict is not True:
            raise NetworkError('The prepared bundle is no longer byte-identical: '+'; '.join(problems))
        if digest(net,net.project_row(proj['slug']),net.store.one('SELECT * FROM functions WHERE id=?',fn['id']))!=item['inputs']:
            raise NetworkError('Build inputs changed while rechecking. Run preparation again.')
        net._record(fn,agent,0,item['source'],0,matched=True,via='local-source-review',
                    effective_settings=result.effective_settings,prepared_header=item['header'],
                    prepared_inputs=expected,prepared_compiler=settings)
        log('Exact source, private header and function-only compiler profile saved together.')
        return {'matched':True,'function':fn['name']}
    finally:net.permuter._let_go(agent,fn)
