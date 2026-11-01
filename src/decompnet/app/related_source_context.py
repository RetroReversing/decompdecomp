"""Offer exact neighbours' private declarations as hypotheses, never as shared-header changes."""
import re

from ..forks.csource import _blank
from .source_context import typedefs
from .source_lead_analysis import features

MAX_NEIGHBOURS = 16
MAX_CONTEXT_BYTES = 128 * 1024
MAX_VARIANTS = 2


def candidates(net, proj, fn, source, header):
    """Same-project ROM context only; require a shared record type and a shared target call."""
    if not getattr(net, 'is_rom', lambda p: False)(proj) or net.fork_slug(proj['slug']):
        return []  # Imported projects must use their existing build's own headers.
    types = {name for name, declaration in typedefs(header).items()
             if '{' in declaration and re.search(r'\b'+re.escape(name)+r'\b', _blank(source))}
    calls = set(features(_instructions(fn['asm']), proj['arch'])['calls'])
    if not types or not calls:
        return []
    if not net.store.one("SELECT 1 FROM sqlite_master WHERE name='verified_matches'"):
        return []
    rows = net.store.all("SELECT f.*,v.header AS verified_header FROM functions f JOIN verified_matches v ON v.function_id=f.id "
                         "WHERE f.project_id=? AND f.status='matched' AND f.best_score=0 AND v.source=f.matched_source "
                         "AND f.id!=? AND f.vram BETWEEN ? AND ? ORDER BY ABS(f.vram-?) LIMIT ?",
                         proj['id'], fn['id'], fn['vram']-65536, fn['vram']+65536, fn['vram'], MAX_NEIGHBOURS)
    result = []
    seen = {header}
    for row in rows:
        shared_calls = calls & set(features(_instructions(row['asm']), proj['arch'])['calls'])
        if not shared_calls or not row['matched_source']:
            continue
        context = row['verified_header']
        if context in seen or len(context.encode()) > MAX_CONTEXT_BYTES:
            continue
        shared_types = types & {name for name, declaration in typedefs(context).items() if '{' in declaration}
        if not shared_types:
            continue
        seen.add(context)
        result.append({'header': context, 'function': row['name'], 'types': sorted(shared_types),
                       'calls': sorted(shared_calls), 'distance': abs(row['vram']-fn['vram'])})
    result.sort(key=lambda r: (-len(r['types']), -len(r['calls']), r['distance']))
    return result[:MAX_VARIANTS]


def _instructions(asm):
    # Normalized disassembly rows are shared with source-lead comparisons, not architecture guesses.
    return [line.split('*/', 1)[-1].strip() for line in (asm or '').splitlines()]
