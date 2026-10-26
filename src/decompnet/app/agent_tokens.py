"""Small agent-facing views and versioned edits; compilation still uses the ordinary shared judge."""

import difflib
import hashlib
import re
import time

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


def brief(net, text, slug=None, agent=None):
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
            text += '\n' + tried_summary(net, fn)
            text += f"\n{fn['name']}: supporting attempt history: get_function(section=\"history\").\n"
            if agent is not None:
                source, revision = candidate(net, agent, slug, fn['name'])
                # Only attach a revision when the exact candidate is present in the claim.
                if source and source.strip() in text:
                    text += f"{fn['name']}: candidate revision {revision}; use compile_patch directly on the candidate above.\n"
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
        key = (agent.id, fn['id'])
        streaks = getattr(net, '_agent_unchanged', None)
        if streaks is None:
            streaks = net._agent_unchanged = {}
        streaks[key] = streaks.get(key, 0) + 1
        detail = 'Instruction diff unchanged from your previous compile (not merely an equal score).'
        if streaks[key] >= 3:
            detail += (' Multiple edits produced identical instructions. Change approach: inspect the differing '
                       'region, try a local permutation search, or test independent alternatives with compile_variants; '
                       'avoid repeating edits the compiler optimizes away.')
    else:
        getattr(net, '_agent_unchanged', {}).pop((agent.id, fn['id']), None)
        # Show newly changed differing rows first, with ordinary first-region fallback.
        changed = [(r[0], f'[{i}] {r[1]}', r[2]) for i,r in enumerate(rows) if r[0]!=' ' and (previous is None or i>=len(previous) or r!=previous[i])]
        detail = 'Changed differing rows (first 12):\n' + scoring.render_rows(changed[:12], 0, max_rows=18, legend=legend, more_hint=False)
    return head + detail + warn + '\nFull/paged instructions: last_diff(function, start=0, limit=40).'


def function_view(net, agent, slug, function, section='all'):
    """Small supporting reads without replaying the candidate and assembly."""
    if section == 'history':
        fn = net._function(function, slug)
        return tried_summary(net, fn) + (history(net, fn) or 'No attempt history.')
    if section == 'candidate':
        return get_candidate(net, agent, slug, function)
    if section not in ('all', 'notes', 'declarations', 'compiler', 'layouts'):
        raise NetworkError('Choose section all, history, candidate, notes, declarations, compiler or layouts.')
    text = net.get_function(function, slug, agent)
    if section == 'all':
        return text + history(net, net._function(function, slug))
    titles = {'notes': ('Notes',), 'declarations': ('Declarations', 'Data it uses'),
              'compiler': ('Compiler',), 'layouts': ('Likely struct layouts',)}[section]
    blocks = re.split(r'(?=^### )', text, flags=re.M)
    selected = [b for b in blocks if any(b.startswith('### '+title) for title in titles)]
    # The preamble contains per-function compiler ranges in ROM mode.
    if section == 'compiler':
        selected.insert(0, blocks[0].split('### Target assembly')[0])
    return '\n'.join(selected) if selected else f'No {section} section; use get_function(section="all") for the full brief.'


def outcome(net, agent, fn, source, text):
    """Track displayed diffs, never substitute this summary for the byte judge."""
    score = re.search(r'\bscore[: ]+(\d+)', text)
    score = 0 if 'MATCH!' in text else int(score[1]) if score else None
    if 'COMPILE ERROR' in text or text.startswith(('error:', '❌')):
        score = None
    kept = net._last_diffs.get((agent.id, fn['id'])) if score is not None else None
    offsets = [text.index(mark) for mark in ('\ncompiler output:', '\nwhen linking your code') if mark in text]
    warnings = text[min(offsets):] if offsets else ''
    value = {'source': source, 'score': score, 'rows': list(kept[0]) if kept else None, 'warnings': warnings}
    if not hasattr(net, '_agent_outcomes'):
        net._agent_outcomes = {}
    net._agent_outcomes[agent.id, fn['id']] = value
    return value


def tried_summary(net, fn):
    rows = net.store.all('SELECT family,SUM(trials) AS trials,MIN(best_score) AS best FROM '
                         '(SELECT * FROM agent_variant_runs WHERE function_id=? ORDER BY id DESC LIMIT 20) '
                         'GROUP BY family ORDER BY trials DESC LIMIT 4', fn['id'])
    if not rows:
        return ''
    return ('Already tried (recent recorded batches): ' + '; '.join(
        f"{r['family']}: {r['trials']} trials, best score {r['best'] if r['best'] is not None else 'unknown'}" for r in rows)
        + '. Historical settings may differ; families are not exhausted. Details: get_function(section="history").\n')


def family_patches(family, old):
    """Bounded, explicit-anchor experiments. The compiler and exact judge decide every candidate."""
    if not isinstance(old, str) or not old:
        raise NetworkError('Supply the exact nonempty declaration or expression to vary.')
    if family == 'casts':
        return [{'old': old, 'new': f'({t})({old})'} for t in ('int', 'unsigned int', 'short', 'unsigned short', 'char', 'unsigned char')]
    if family == 'signedness':
        pairs = {'int': 'unsigned int', 'short': 'unsigned short', 'char': 'unsigned char',
                 'long': 'unsigned long', 's8': 'u8', 's16': 'u16', 's32': 'u32', 's64': 'u64'}
        pairs.update({v:k for k,v in list(pairs.items())})
        match = re.match(r'('+'|'.join(re.escape(k) for k in sorted(pairs,key=len,reverse=True))+r')\b', old)
        if not match:
            raise NetworkError('signedness needs an anchor starting with a supported integer type.')
        return [{'old':old, 'new':pairs[match[1]]+old[match.end():]}]
    if family == 'declaration_order':
        lines = old.splitlines()
        if len(lines) != 2 or not all(re.fullmatch(r'\s*(?:unsigned\s+|signed\s+)?(?:int|short|long|char|float|double|[su](?:8|16|32|64))\s+\*?\s*[A-Za-z_]\w*\s*;\s*', line) for line in lines):
            raise NetworkError('declaration_order needs exactly two adjacent simple uninitialized declarations.')
        return [{'old':old,'new':'\n'.join(reversed(lines))}]
    raise NetworkError('Choose family casts, signedness or declaration_order.')


def local_family(net, agent, slug, function, revision, family, old):
    return variants(net, agent, slug, function, revision, family_patches(family, old), family)


def variants(net, agent, slug, function, revision, patches, family="explicit patches"):
    """Independent alternatives against one snapshot, never a cumulative patch chain."""
    from .tools import _compile
    if not isinstance(patches, list) or not 1 <= len(patches) <= 6:
        raise NetworkError('Send 1 to 6 independent patches with old and new text.')
    fn = net._function(function, slug)
    with net.attempt_turn(agent, fn):
        source, actual = candidate(net, agent, slug, function)
        if revision != actual:
            raise NetworkError('Candidate changed. Call get_candidate before trying variants.')
        candidates = []
        for p in patches:
            if (not isinstance(p, dict) or not isinstance(p.get('old'), str) or not p['old']
                    or not isinstance(p.get('new'), str) or source.count(p['old']) != 1):
                raise NetworkError('Every patch needs old text occurring exactly once and new replacement text.')
            candidates.append(source.replace(p['old'], p['new'], 1))
        results = ['Independent variants against revision '+revision+' (not cumulative):']
        last = None
        groups = {}
        scores = []
        baseline = getattr(net, '_agent_outcomes', {}).get((agent.id, fn['id']))
        if baseline and baseline['source'] != source:
            baseline = None
        if baseline is None:
            prior = net.store.one('SELECT score FROM attempts WHERE function_id=? AND source=? ORDER BY id DESC LIMIT 1', fn['id'],source)
            if prior and prior['score'] is not None:
                baseline = {'score':prior['score'], 'rows':None}
        if family != 'explicit patches':
            results.append('Generated replacements: '+ '; '.join(f"{i}: {p['new'][:100]}" for i,p in enumerate(patches,1)))
        began = time.monotonic()
        for i, src in enumerate(candidates, 1):
            if last is not None and time.monotonic() - began >= 60:
                results.append('Stopped before the next trial: 60-second batch budget reached. Resume with the current revision.')
                break
            try:
                result = _compile(net, agent, slug, function, src)
            except NetworkError as e:
                results.append(f'{i}: stopped: {e}')
                break
            last = i
            measured = getattr(net, '_agent_outcomes', {}).get((agent.id, fn['id']))
            measured = measured if measured and measured['source'] == src else None
            score = measured['score'] if measured else None
            rows = measured['rows'] if measured else None
            if score is not None:
                scores.append(score)
            # Group only successful, identical displayed diffs AND scores. Errors stay distinct;
            # identical text does not prove byte identity and never skips a compile.
            key = (score, repr(rows), measured.get('warnings',''), 'MATCH!' in result) if rows is not None else ('unavailable', i)
            if key in groups:
                groups[key][0].append(i)
            else:
                base_score = baseline['score'] if baseline else None
                comparison = ('baseline unavailable' if score is None or base_score is None else
                              f"{'improved' if score < base_score else 'worse' if score > base_score else 'equal'} vs starting score {base_score}")
                changes = 'instruction changes unavailable'
                if rows is not None and baseline and baseline['rows'] is not None:
                    before = baseline['rows']
                    count = sum(a != b for a,b in zip(before,rows)) + abs(len(before)-len(rows))
                    changes = f'{count} displayed diff rows changed vs start'
                summary = f'score {score if score is not None else "unavailable"}; {comparison}; {changes}'
                if rows is not None:
                    summary += f"; {sum(r[0] != ' ' for r in rows)} displayed mismatch rows vs target"
                if measured and measured.get('warnings'):
                    summary += '; diagnostics: '+measured['warnings'][:250]
                if 'MATCH!' in result:
                    summary += '; MATCH! verified by normal judge'
                if score is None:
                    summary += '; ' + '\n'.join(result.splitlines()[:4])[:400]
                if 'Max tries reached' in result:
                    summary += '; Max tries reached'
                groups[key] = ([i], summary)
            if 'Max tries reached' in result:
                results.append('Max tries reached; no further variants submitted.')
            if net._function(function, slug)['status'] != 'open' or 'Max tries reached' in result:
                break
        for indices, summary in groups.values():
            results.append('Variants '+','.join(map(str,indices))+': '+summary)
        if baseline:
            results.append('Baseline is a previously observed compile of the starting C; settings may have changed. Grouping uses displayed diffs, not proof of byte identity.')
        with net.store.tx() as db:
            db.execute('INSERT INTO agent_variant_runs(function_id,family,revision,trials,best_score,created_at) VALUES(?,?,?,?,?,?)',
                       (fn['id'],family,revision,last or 0,min(scores) if scores else None,time.time()))
        current_source, current = candidate(net, agent, slug, function)
        current_index = next((i for i, src in enumerate(candidates, 1) if src == current_source), None)
        results.append(f'Current candidate revision {current}; last submitted variant {last}; current source variant {current_index or "retained candidate"}. '
                       'Best attempts are saved normally. last_diff shows the last completed trial; get_candidate recovers the current C. Cached or rejected trials may retain the earlier candidate.')
        return '\n'.join(results)
