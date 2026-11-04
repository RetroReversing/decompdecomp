"""Explicit public metadata for a directory, without source, assembly, paths or room credentials.

This is served only by the restricted room server. Enabling a public room alone never
publishes its metadata: the owner must also enable this separate switch.
"""
from __future__ import annotations

import hashlib
import json
import re

from starlette.responses import JSONResponse
from starlette.routing import Route

from ..network import IN_BUILD, NetworkError

MAX_FUNCTIONS = 20000


def configure(net, slug: str, enabled: bool, proof: str) -> None:
    """Only the guarded desktop API calls this; the public server cannot change consent."""
    if enabled and not re.fullmatch(r'[a-f0-9]{64}', proof):
        raise NetworkError('Paste the publishing code from the directory (64 letters and digits).')
    with net.store.tx() as db:
        proj = db.execute('SELECT config FROM projects WHERE slug=?', (slug,)).fetchone()
        if proj is None:
            raise NetworkError('Choose an existing project.')
        cfg = json.loads(proj['config'])
        cfg.setdefault('collab', {})['directory'] = {'enabled': enabled, 'proof': proof if enabled else ''}
        db.execute('UPDATE projects SET config=? WHERE slug=?', (json.dumps(cfg), slug))


def snapshot(net, room) -> dict:
    """Project-mode and ROM-mode functions share this strict, metadata-only allowlist."""
    cfg = room.cfg
    if room.settings['visibility'] != 'public' or not cfg.get('collab', {}).get('directory', {}).get('enabled'):
        raise NetworkError('Public directory metadata is disabled.')
    proj = net.project_row(room.slug)
    rows = net.store.all(f'SELECT name,size,n_insns,status,best_score FROM functions WHERE project_id=? AND {IN_BUILD} '
                         'ORDER BY vram,unit,id LIMIT ?', proj['id'], MAX_FUNCTIONS + 1)
    if len(rows) > MAX_FUNCTIONS:
        raise NetworkError('This project exceeds the directory limit of 20,000 functions.')
    # Totals come from the same read snapshot as rows, avoiding inconsistent rollups during a match.
    functions = [[r['name'], r['size'] or 0, r['status'] if r['status'] in
                  ('open', 'matched', 'asm', 'not_c', 'set_aside') else 'open',
                  r['best_score'], r['n_insns'] or 0] for r in rows]
    return {'version': 1, 'room': room.slug, 'title': proj['name'] or cfg.get('rom', {}).get('title') or room.slug,
            'platform': net.console(proj).name,
            'compiler': str(cfg.get('compiler', {}).get('toolchain') or ('Project build' if room.fork else '')),
            'proof': cfg['collab']['directory']['proof'],
            'summary': {'functions': len(rows), 'matched': sum(r['status'] == 'matched' for r in rows),
                        'bytes': sum(r['size'] or 0 for r in rows),
                        'matched_bytes': sum(r['size'] or 0 for r in rows if r['status'] == 'matched')},
            'functions': functions}


def routes(net, rooms):
    async def project(request):
        import anyio

        slug = request.query_params.get('room', '')
        if not slug:
            public = [r for r in rooms.values() if r.settings['visibility'] == 'public' and
                      r.cfg.get('collab', {}).get('directory', {}).get('enabled')]
            if len(public) == 1:
                slug = public[0].slug
        room = rooms.get(slug)
        if room is None:
            return JSONResponse({'error': 'Choose an open room with public directory metadata enabled.'}, status_code=404)
        try:
            data = await anyio.to_thread.run_sync(lambda: snapshot(net, room))
        except NetworkError as error:
            return JSONResponse({'error': str(error)}, status_code=410)
        raw = json.dumps(data, separators=(',', ':'))
        etag = '"' + hashlib.sha256(raw.encode()).hexdigest() + '"'
        from starlette.responses import Response
        headers = {'Cache-Control': 'no-store', 'ETag': etag, 'X-Content-Type-Options': 'nosniff'}
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return Response(raw, media_type='application/json', headers=headers)

    return [Route('/directory/project', project)]
