"""Small agent-facing views and versioned edits; compilation still uses the ordinary shared judge."""

import difflib
import hashlib
import re

from .. import scoring
from ..network import NetworkError


def candidate(net, agent, slug, function):
    fn = net._function(function, slug)
    row = net.store.one('SELECT source,created_at FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1', fn['id'], agent.id)
    claim = net.store.one('SELECT claimed_at FROM claims WHERE function_id=? AND agent_id=?', fn['id'], agent.id)
    row = row if row and claim and row['created_at'] >= claim['claimed_at'] else None
    source = row['source'] if row else fn['matched_source'] or fn['best_source'] or fn['seed'] or ''
    if not source and net.fork_slug(slug):
        _, fp, _ = net.forks.get(slug)
        source = net.forks._current_source(fp, fn) or ''
    return source, hashlib.sha256(source.encode()).hexdigest()[:20]


def get_candidate(net, agent, slug, function):
    source, revision = candidate(net, agent, slug, function)
    return f'Candidate revision {revision}\n```c\n{source}\n```'


def patch(net, agent, slug, function, revision, old, new, repair_note='', repair_category='other', repair_automation=''):
    fn = net._function(function, slug)
    with net.attempt_turn(agent, fn):
        source, actual = candidate(net, agent, slug, function)
        if revision != actual:
            raise NetworkError('Candidate changed. Call get_candidate and apply the patch to its current revision.')
        if not old or source.count(old) != 1:
            raise NetworkError('Patch old text must occur exactly once. Call get_candidate to check the current source.')
        from .tools import _compile
        return _compile(net, agent, slug, function, source.replace(old, new, 1), repair_note, repair_category, repair_automation)


def remember(net, agent, fn, before):
    latest = net.store.one('SELECT id,source,score FROM attempts WHERE function_id=? AND agent_id=? ORDER BY id DESC LIMIT 1', fn['id'], agent.id)
    if not latest or before and before['id'] == latest['id']:
        return
    note = 'compile error' if latest['score'] is None else 'score '+str(latest['score'])
    if before:
        changes = [s for s in difflib.ndiff((before['source'] or '').splitlines(), (latest['source'] or '').splitlines()) if s.startswith(('- ', '+ '))]
        if changes:
            note += '; '+ ' / '.join(line.strip()[:100] for line in changes[:2])
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO attempt_hints VALUES(?,?,?)', (latest['id'], fn['id'], note[:260]))


def history(net, fn):
    rows = net.store.all('SELECT a.agent_id,a.score,a.source,h.summary FROM attempts a LEFT JOIN attempt_hints h ON h.attempt_id=a.id WHERE a.function_id=? ORDER BY a.id DESC LIMIT 100', fn['id'])
    if not rows:
        return ''
    scores = [r['score'] for r in rows if r['score'] is not None]
    fingerprints = []
    seen = set()
    for r in rows:
        key = hashlib.sha256((r['source'] or '').encode()).hexdigest()[:12]
        if key not in seen:
            fingerprints.append(f"{key}: {r['summary'] or ('compile error' if r['score'] is None else 'score '+str(r['score']))}")
            seen.add(key)
        if len(fingerprints) == 4:
            break
    auto = net.store.one('SELECT COUNT(*) AS n FROM reuse_trials WHERE function_id=?', fn['id'])
    return (f"\nAutomatic reuse/repair trials recorded: {auto['n']}.\nRecent history: {len(rows)} attempts, {len({r['agent_id'] for r in rows})} agents; "
            f"best historical score {min(scores) if scores else 'none'}. Latest distinct sources: "
            + '; '.join(fingerprints) + '. Scores may use older headers/settings. Read existing notes before repeating approaches.\n')


def brief(net, text, slug=None):
    # Keep the complete candidate and user instructions. Long target listings and auxiliary sections remain
    # available through get_function / get_assembly, rather than repeated on every claim.
    def assembly(m):
        lines = m[1].splitlines()
        return '```asm\n'+'\n'.join(lines[:24])+('\n… use get_assembly for later lines.' if len(lines)>24 else '')+'\n```'
    text = re.sub(r'```asm\n(.*?)\n```', assembly, text, flags=re.S)
    # Keep compiler facts, declarations, candidate C and user instructions. Supporting evidence is
    # available through get_function without loading it for every claim (ROM and imported projects).
    optional = ('Notes from previous agents on this function', 'Notes', 'Where it lives',
                'Other games and local SDK signatures', 'Looks like', 'Likely struct layouts')
    text = re.sub(r'^### ([^\n]+)\n(.*?)(?=^#{2,3} )',
                  lambda m: ('### '+m[1]+'\nSupporting details: get_function(function).\n'
                             if any(m[1].startswith(title) for title in optional) else m[0]),
                  text, flags=re.M | re.S)
    # A batch can contain more than one claimed function. Resolve each stored name from its brief header.
    for name in re.findall(r'^## CLAIMED: (.+?)(?:  \([^\n]*\))?$', text, re.M):
        if slug and name.startswith(slug + '/'):
            name = name[len(slug)+1:]
        try:
            fn = net._function(name.strip(), slug)
            text += f"\n{fn['name']}: supporting attempt history is available through get_function(function).\n"
        except NetworkError:
            pass
    return text


def assembly(net, slug, function, start=0, limit=60):
    fn = net._function(function, slug)
    if net.fork_slug(slug):
        _, fp, _ = net.forks.get(slug)
        unit = fp.unit(fn['unit'])
        lines = fp.diff(unit, net.forks._meta(fn).get('symbol') or fn['name']).target_asm().splitlines()
    else:
        lines = net._asm_text(fn, net._project(slug)).splitlines()
    start, limit = max(0, int(start)), max(1, min(200, int(limit)))
    return f'Target assembly lines {start}:{min(start+limit,len(lines))} of {len(lines)}\n'+'\n'.join(lines[start:start+limit])


def diff_page(net, agent, slug, function, context=1, start=0, limit=40):
    fn = net._function(function, slug)
    kept = net._last_diffs.get((agent.id, fn['id']))
    if kept is None:
        raise NetworkError('No diff saved for you. Compile a candidate first.')
    rows, legend = kept
    start, limit = max(0, int(start)), max(1, min(200, int(limit)))
    page = rows[start:start+limit]
    return (f'Diff rows {start}:{min(start+limit,len(rows))} of {len(rows)}; next start={start+limit}. No compile.\n'
            + scoring.render_rows(page, max(0,min(3,int(context))), max_rows=limit*7+10, legend=legend, more_hint=False))


def feedback(net, agent, slug, function, text, previous):
    fn = net._function(function, slug)
    kept = net._last_diffs.get((agent.id, fn['id']))
    if 'REPEAT:' in text:
        # The cache already avoids compilation. Avoid replaying its entire old tool response too.
        score = re.search(r'(?:score |score: )(\d+)', text)
        head = text.split("That compile's result:")[0]
        if score:
            return head + f"\nCached score {score[1]}.\nUse last_diff for details."
        failure = text.split("That compile's result:\n", 1)[-1]
        return head + '\nCached failure: ' + '\n'.join(failure.splitlines()[:4])[:600]
    if not kept or 'MATCH!' in text or 'ERROR' in text:
        return text
    rows, legend = kept
    if 'markers:' not in text:
        return text
    head = text.split('markers:', 1)[0]
    positions = [text.index(marker) for marker in ('\ncompiler output:', '\nwhen linking your code') if marker in text]
    warn = text[min(positions):] if positions else ''
    if previous == rows:
        detail = 'Instruction diff unchanged from your previous compile (not merely an equal score).'
    else:
        # Show newly changed differing rows first, with ordinary first-region fallback.
        changed = [(r[0], f'[{i}] {r[1]}', r[2]) for i,r in enumerate(rows) if r[0]!=' ' and (previous is None or i>=len(previous) or r!=previous[i])]
        detail = 'Changed differing rows (first 12):\n' + scoring.render_rows(changed[:12], 0, max_rows=18, legend=legend, more_hint=False)
    return head + detail + warn + '\nFull/paged instructions: last_diff(function, start=0, limit=40).'
