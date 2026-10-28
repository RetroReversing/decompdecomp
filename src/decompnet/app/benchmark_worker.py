"""One comparison trial in a separate process, with private database, files and MCP server."""
from __future__ import annotations

import json
import signal
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings

from ..network import NetworkError
from .agents import Manager
from .benchmarks import target_digest, write
from .engine import RomNetwork
from .tools import build_mcp


def result(net, slug, baseline, session, manager, began):
    rows=net.store.all('SELECT id,name,size,n_insns,best_score,status,target,relocs FROM functions WHERE project_id=?',net.project_row(slug)['id'])
    matched=[]
    matched_ids=[]
    improved=[]
    changed=[]
    regressions=[]
    recovered=0
    gain=0.0
    total=sum(v['size'] for v in baseline.values() if v.get('eligible',True))
    for r in rows:
        old=baseline.get(str(r['id']))
        if old is None or not old.get('eligible',True): continue
        if old.get('target_digest') and target_digest(r['target'],r['relocs'],r['size'],r['n_insns'])!=old['target_digest']:
            changed.append(r['name'])
            if old['matched']: regressions.append(r['name'])
            continue
        if old['matched'] and r['status']!='matched': regressions.append(r['name'])
        if r['status']=='matched' and not old['matched']:
            matched.append(r['name'])
            matched_ids.append(str(r['id']))
            recovered+=r['size'] or 0
        score=0 if r['status']=='matched' else r['best_score']
        prior=old['score'] if old['score'] is not None else old['insns']*100
        if score is not None and score>=0 and score<prior and not old['matched']:
            improved.append(r['name'])
            gain+=max(0,prior-score)/max(1,(r['n_insns'] or 0)*100)*100
    present={str(r['id']) for r in rows}
    changed.extend(key for key in baseline if key not in present)
    regressions.extend(key for key,value in baseline.items() if key not in present and value['matched'])
    records=net.store.all('SELECT * FROM helper_sessions WHERE helper_id=?',session.id)
    usage={k:sum(r[k] for r in records) for k in ('input_tokens','output_tokens','cache_read_tokens','cache_write_tokens','tokens','usd','compiles')}
    estimate=manager.snapshot()[0]['estimated_cost'] if manager.sessions else None
    return {'state':'failed' if regressions else 'complete','regressions':regressions,'changed_targets':changed,'seconds':time.time()-began,'stop_reason':session.why,'matches':len(matched),
            'matched_ids':matched_ids,'matched_functions':matched,'improved':len(improved),'improved_functions':improved,
            'verified_bytes':recovered,'coverage_gain':100*recovered/total if total else None,
            'sum_percentage_point_gain':gain,'usage':usage,'api_equivalent_estimate':estimate,
            'bytes_per_million_tokens':recovered*1000000/usage['tokens'] if usage['tokens'] else None,
            'usage_note':'Only provider-reported usage. Interrupted calls may be absent; dollar estimates are not subscription charges.'}


def redact(value,secret):
    if not secret: return value
    if isinstance(value,str): return value.replace(secret,'[redacted]')
    if isinstance(value,list): return [redact(v,secret) for v in value]
    if isinstance(value,dict): return {k:redact(v,secret) for k,v in value.items()}
    return value


def run(folder,request):
    net=RomNetwork(folder/'trial.db',folder)
    # Automatic free workers would contaminate which model earned progress.
    net.on_match=lambda *a,**kw: None
    net.cascades.configure(False)
    listener=socket.socket()
    listener.bind(('127.0.0.1',0))
    listener.listen(128)
    port=listener.getsockname()[1]
    manager=Manager(net,folder/'state',f'http://127.0.0.1:{port}/mcp')
    manager.home.mkdir(parents=True,exist_ok=True)
    manager._prepare=lambda slug: None
    stopping=threading.Event()
    for name in ('SIGTERM','SIGINT'):
        if hasattr(signal,name): signal.signal(getattr(signal,name),lambda *a: stopping.set())
    def agent_of(ctx):
        headers=ctx.headers or {}
        token=headers.get('authorization') or headers.get('Authorization') or ''
        agent=net.authenticate(token.removeprefix('Bearer ').strip())
        slug=manager.project_of(agent.id)
        if not slug or stopping.is_set(): raise NetworkError('The comparison trial has stopped.')
        return agent,slug
    mcp=build_mcp(net,agent_of,observe=manager.record_agent)
    from mcp_types import ListToolsResult, PaginatedRequestParams

    from . import tools
    async def scoped(ctx,params):
        auth=(getattr(ctx.request,'headers',None) or {}).get('authorization','')
        try: agent=net.authenticate(auth.removeprefix('Bearer ').strip())
        except NetworkError: return ListToolsResult(tools=[])
        slug=manager.project_of(agent.id)
        if not slug: return ListToolsResult(tools=[])
        keep=set(tools.project_tools(net,slug))
        selected=[t for t in await mcp.list_tools() if t.name in keep]
        manager.record_agent(agent,slug,'tool_definitions',[t.model_dump(mode='json') for t in selected])
        return ListToolsResult(tools=selected)
    mcp._lowlevel_server.add_request_handler('tools/list',PaginatedRequestParams,scoped)
    security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                       allowed_hosts=[f'127.0.0.1:{port}'],allowed_origins=[f'http://127.0.0.1:{port}'])
    server=uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(transport_security=security,host='127.0.0.1'),
                                       host='127.0.0.1',port=port,log_level='warning',timeout_graceful_shutdown=30))
    thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started: break
            if not thread.is_alive(): raise NetworkError('The comparison MCP server could not start.')
            time.sleep(.05)
        if not server.started: raise NetworkError('The comparison MCP server did not become ready.')
        if (folder/'stop').exists():
            write(folder/'result.json',{'state':'cancelled','stop_reason':'Cancelled before model start.'})
            return
        sid=manager.start(request['kind'],request['slug'],1,request['options'])[0]
        s=manager.sessions[sid]
        while s.state in ('starting','working','waiting') and not stopping.wait(.5):
            if (folder/'stop').exists():
                stopping.set()
                break
        if stopping.is_set(): manager.stop(sid)
        if s.thread: s.thread.join(timeout=30)
        if s.thread and s.thread.is_alive():
            raise NetworkError('The worker has not finished stopping; usage may be incomplete.')
        server.should_exit=True
        thread.join(timeout=35)
        if thread.is_alive():
            raise NetworkError('In-flight checks have not stopped; this trial cannot report a final score.')
        answer=result(net,request['slug'],request['baseline'],s,manager,s.started_at)
        answer['worker_state']=s.state
        if s.state=='paused': answer['state']='failed'
        if stopping.is_set(): answer['state']='cancelled'
        write(folder/'result.json',redact(answer,request['options'].get('api_key')))
    finally:
        manager.shutdown()
        server.should_exit=True
        thread.join(timeout=10)
        listener.close()


def main():
    folder=Path(sys.argv[1]).resolve()
    request={}
    try:
        request=json.load(sys.stdin)
        run(folder,request)
    except BaseException as e:
        write(folder/'result.json',redact({'state':'failed','error':str(e)},(request.get('options') or {}).get('api_key')))
        raise


if __name__=='__main__': main()
