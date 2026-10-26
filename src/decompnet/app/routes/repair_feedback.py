"""Review and locally export matching repairs, independently of agent activity/transcript routes."""
from starlette.responses import JSONResponse, StreamingResponse

from ...network import NetworkError


def register(app):
    feedback = app.net.repair_feedback

    @app.mcp.custom_route('/api/repair-examples', methods=['GET', 'POST'])
    async def examples(request):
        if bad := app.guard(request, write=request.method == 'POST'):
            return bad
        try:
            q = request.query_params
            data = await app.body(request) if request.method == 'POST' else {}
            slug = '' if (data.get('all') if data else q.get('all') == '1') else (data.get('slug') or q.get('slug', ''))
            if not slug and not (data.get('all') if data else q.get('all') == '1'):
                raise NetworkError('Choose a project or explicitly select all projects.')
            if request.method == 'POST':
                return JSONResponse(await app.call(feedback.review, slug, int(data['id']), str(data['status']),
                                                   str(data.get('category', '')), data.get('note')))
            if q.get('id'):
                return JSONResponse(await app.call(feedback.detail, slug, int(q['id'])))
            return JSONResponse(await app.call(feedback.listing, slug, q.get('category', ''), q.get('status', ''),
                                               int(q.get('before', 0)), q.get('comparable') == '1'))
        except (NetworkError, ValueError, KeyError) as error:
            return app.fail(error)

    @app.mcp.custom_route('/api/repair-examples/export', methods=['GET'])
    async def export(request):
        if bad := app.guard(request):
            return bad
        try:
            q = request.query_params
            slug = '' if q.get('all') == '1' else q.get('slug', '')
            if not slug and q.get('all') != '1':
                raise NetworkError('Choose a project or explicitly select all projects.')
            feedback._where(slug, q.get('category', ''), q.get('status', ''), q.get('comparable') == '1')
            chunks = feedback.export(slug, q.get('category', ''), q.get('status', ''), q.get('include_source') == '1', q.get('comparable') == '1')
            return StreamingResponse(chunks, media_type='application/json',
                                     headers={'Content-Disposition': 'attachment; filename="repair-examples.json"'})
        except (NetworkError, ValueError) as error:
            return app.fail(error)
