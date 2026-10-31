"""Explicit verified export-to-checkout synchronization; never commits or pushes on the user's behalf."""
import threading

from starlette.responses import JSONResponse

from ...network import NetworkError
from .. import repository_sync


def register(app):
    lock=threading.Lock()

    @app.mcp.custom_route('/api/repository-sync', methods=['GET','POST'])
    async def sync(request):
        if bad:=app.guard(request,write=request.method=='POST'):
            return bad
        try:
            if request.method=='GET':
                return JSONResponse(await app.call(repository_sync.status,app.net,str(request.query_params.get('slug',''))))
            data=await app.body(request)
            slug=str(data.get('slug',''));folder=str(data.get('folder','')).strip()
            app.net.project_row(slug)
            if not folder:raise NetworkError('Choose the root folder of your Git checkout.')
            # Reserve before starting a job; a second click must not launch another build.
            if not lock.acquire(blocking=False):raise NetworkError('A repository sync is already running.')
            def work(log):
                try:return repository_sync.run(app.net,slug,folder,log)
                finally:lock.release()
            try:
                job=app.jobs.start('repository-sync',work,cancel_message='Repository sync stopped. Unpublished changes were discarded.')
            except BaseException:
                lock.release();raise
            return JSONResponse({'job':job})
        except (NetworkError,ValueError,KeyError,OSError) as error:
            return app.fail(error)
