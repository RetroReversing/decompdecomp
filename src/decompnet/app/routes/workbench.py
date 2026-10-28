"""Queue previews, per-agent controls, model evidence and observed diff summaries."""
from starlette.responses import JSONResponse

from ...network import NetworkError
from .. import agent_workbench


def register(app):
    from ..benchmarks import Benchmarks
    comparisons=Benchmarks(app)
    previous_stop=app.manager.on_stop
    def stop():
        comparisons.shutdown()
        previous_stop()
    app.manager.on_stop=stop

    @app.mcp.custom_route('/api/comparisons',methods=['GET','POST'])
    async def comparison(request):
        if bad:=app.guard(request,write=request.method=='POST'): return bad
        try:
            if request.method=='GET':
                return JSONResponse(await app.call(comparisons.inventory,request.query_params.get('slug','')))
            data=await app.body(request)
            action=data.get('action','checkpoint')
            if action=='checkpoint':
                slug=str(data.get('slug',''))
                return JSONResponse({'job':app.jobs.start('comparison-checkpoint',lambda log:comparisons.checkpoint(slug,log))})
            identifier=str(data.get('id',''))
            comparisons.folder(identifier)
            if action=='stop':
                comparisons.stop(identifier)
                return JSONResponse({'stopping':True})
            if action!='run': raise NetworkError('Choose checkpoint, run or stop.')
            if data.get('confirmed') is not True: raise NetworkError('Confirm that starting these model trials uses your configured accounts and allowance.')
            return JSONResponse({'job':app.jobs.start('comparison-run',lambda log:comparisons.run(
                identifier,data.get('plans'),data.get('budget') or {},data.get('selection') or {},
                data.get('allow_builds') is True,log))})
        except (NetworkError,ValueError,OSError) as e: return app.fail(e)

    @app.mcp.custom_route('/api/agents/queue',methods=['GET'])
    async def queue(request):
        if bad:=app.guard(request): return bad
        try:
            return JSONResponse(await app.call(agent_workbench.preview,app.manager,request.query_params.get('id','')))
        except (NetworkError,ValueError) as e: return app.fail(e)

    @app.mcp.custom_route('/api/agents/controls',methods=['POST'])
    async def controls(request):
        if bad:=app.guard(request,write=True): return bad
        data=await app.body(request)
        try:
            values=await app.call(app.manager.set_controls,str(data.get('id','')),data.get('limits') or {},data.get('compiler_first',False))
            return JSONResponse({'limits':values})
        except (NetworkError,ValueError) as e: return app.fail(e)

    @app.mcp.custom_route('/api/function/models',methods=['GET'])
    async def models(request):
        if bad:=app.guard(request): return bad
        try:
            return JSONResponse(await app.call(agent_workbench.models,app.manager,request.query_params.get('slug',''),request.query_params.get('name','')))
        except (NetworkError,ValueError) as e: return app.fail(e)

    @app.mcp.custom_route('/api/function/diagnosis',methods=['GET'])
    async def diagnosis(request):
        if bad:=app.guard(request): return bad
        try:
            d=await app.call(app.net.function_diff,request.query_params.get('slug',''),request.query_params.get('name',''),'best')
            return JSONResponse(agent_workbench.diagnosis(d))
        except (NetworkError,ValueError) as e: return app.fail(e)
