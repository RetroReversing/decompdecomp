"""A bounded, token-free search using verified C from similar functions in other ROM projects.

Mnemonic similarity is only a shortlist. Relocations must line up before names can be transferred, and every
candidate goes through the ordinary compiler/link judge. Small immediate differences can suggest literal
edits; they never change the target or count as evidence of a match on their own.
"""

from __future__ import annotations

import json
import re
import time

from .. import similarity
from ..project import compiler_for

MIN_BYTES, MAX_BYTES = 32, 3200
MIN_SIMILARITY = .9
MAX_CANDIDATES = 3
MAX_POOL = 400
MAX_ATTEMPTS = 128
MAX_SECONDS = 120
# Consume comments, strings and identifiers whole, so edits never touch a symbol's address or documentation.
TOKENS = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
                    r'[A-Za-z_]\w*|(?<![\w.])(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:[uUlL]*)(?![\w.])', re.S)
INTEGER = re.compile(r'(0[xX][0-9a-fA-F]+|[0-9]+)([uUlL]*)\Z')
IMMEDIATE_OPS = {8, 9, 10, 11, 12, 13, 14}  # arithmetic/logical immediates, never branches or memory offsets
UNSIGNED_OPS = {12, 13, 14}


def relocations(fn) -> list[tuple[int, int, int]]:
    return sorted(tuple(int(v) for v in r[:3]) for r in json.loads(fn['relocs'] or '[]') if len(r) >= 3)


def literal_variants(source: str, a, b, big: bool) -> list[str]:
    """Try at most two unambiguous literal substitutions supported by aligned immediate instructions.

    Only singleton, nonnegative C literals are changed. Repeated numbers, signed expressions, split address
    constants and changed instruction layouts need more context than this small search can establish.
    """
    left, right = a['target'] or b'', b['target'] or b''
    if len(left) != len(right) or len(left) % 4:
        return []
    relocated = {off for off, _, _ in relocations(a)} | {off for off, _, _ in relocations(b)}
    changes = {}
    order = 'big' if big else 'little'
    for off in range(0, len(left), 4):
        x, y = int.from_bytes(left[off:off + 4], order), int.from_bytes(right[off:off + 4], order)
        if x == y or off in relocated:
            continue
        op = x >> 26
        if x >> 16 != y >> 16 or op not in IMMEDIATE_OPS:
            return []
        old, new = x & 0xffff, y & 0xffff
        if op not in UNSIGNED_OPS and (old >= 0x8000 or new >= 0x8000):
            return []
        if old in changes and changes[old] != new:
            return []
        changes[old] = new
    if not changes or len(changes) > 2:
        return []
    occurrences = {}
    for token in TOKENS.finditer(source):
        if m := INTEGER.fullmatch(token.group()):
            value = int(m[1], 16 if m[1].lower().startswith('0x') else 10)
            occurrences.setdefault(value, []).append((token, m[2]))
    replacements = {}
    for old, new in changes.items():
        hits = occurrences.get(old, [])
        if len(hits) != 1:
            continue
        token, suffix = hits[0]
        if source[:token.start()].rstrip().endswith('-'):
            continue
        replacements[token.start()] = (token.end(), str(new) + suffix)
    if not replacements:
        return []
    result = source
    for start, (end, value) in sorted(replacements.items(), reverse=True):
        result = result[:start] + value + result[end:]
    return [result]


def run(twins, slug: str, job) -> int:
    """Try a few close candidates per function after exact transfers, with a pass-wide time/compile budget."""
    from .twins import _declared, apply, rom_renames

    net = twins.net
    proj = net.project_row(slug)
    # Project mode needs alignment of object relocations and file-context adaptation, not ROM addresses.
    # Other processors need their own instruction reader before mnemonic ranking is meaningful.
    if net.fork_slug(slug) or not net.is_rom(proj) or proj['arch'] != 'mips' or slug in net.links:
        return 0
    cfg = net.cfg(proj)
    others = {p['id']: p for p in net.store.all(
        'SELECT * FROM projects WHERE id!=? AND arch=? AND big_endian=?',
        proj['id'], proj['arch'], proj['big_endian']) if net.is_rom(p) and p['slug'] not in net.links}
    deadline = time.monotonic() + MAX_SECONDS
    for other in others.values():
        if job.stop.is_set() or time.monotonic() >= deadline:
            return 0
        twins.index_project(other)
    agent, _ = net.local_agent('autopilot')
    mine = twins.prints(proj)
    syms_b = net.symbols(proj['id'])
    source_cache, ops_cache = {}, {}
    attempted = matched = 0
    for b in net.store.all('SELECT * FROM functions WHERE project_id=? AND status=\'open\' ORDER BY id', proj['id']):
        if job.stop.is_set() or attempted >= MAX_ATTEMPTS or time.monotonic() >= deadline:
            break
        pb = mine.get(b['id'])
        if pb is None or pb.asm or not MIN_BYTES <= pb.size <= MAX_BYTES:
            continue
        if net.store.one('SELECT 1 FROM claims WHERE function_id=?', b['id']):
            continue
        rows = net.store.all(
            'SELECT f.*,i.compiler,i.fingerprint FROM cross_game_index i '
            'JOIN functions f ON f.id=i.function_id AND f.status=\'matched\' '
            'JOIN projects p ON p.id=i.project_id WHERE i.project_id!=? AND p.arch=? AND p.big_endian=? '
            'AND i.compiler=? AND i.size BETWEEN ? AND ? AND i.fingerprint!=? '
            'ORDER BY ABS(i.size-?),i.function_id LIMIT ?',
            proj['id'], proj['arch'], proj['big_endian'], twins._compiler_key(compiler_for(cfg, b['vram'])),
            max(MIN_BYTES, int(pb.size * .9)), min(MAX_BYTES, int(pb.size / .9)), pb.key, pb.size, MAX_POOL)
        layout = [(o, t) for o, t, _ in relocations(b)]
        rows = [a for a in rows if a['project_id'] in others and a['matched_source']
                and [(o, t) for o, t, _ in relocations(a)] == layout]
        if not rows:
            continue
        cpu = net.console(proj).cpu_variant
        ops = similarity.mips_mnemonics(b['target'], bool(proj['big_endian']), b['vram'] or 0, cpu)
        for a in rows:
            if a['id'] not in ops_cache:
                other_cpu = net.console(others[a['project_id']]).cpu_variant
                ops_cache[a['id']] = similarity.mips_mnemonics(a['target'], bool(proj['big_endian']),
                                                            a['vram'] or 0, other_cpu)
        by_id = {str(a['id']): a for a in rows}
        ranked = similarity.rank(ops, {key: ops_cache[a['id']] for key, a in by_id.items()},
                                 top=MAX_CANDIDATES, floor=MIN_SIMILARITY)
        for key, score in ranked:
            a = by_id[key]
            other = others[a['project_id']]
            if other['id'] not in source_cache:
                source_cache[other['id']] = (twins.prints(other), net.symbols(other['id']),
                                              net._context(other['id'])['header'])
            prints, syms_a, header_a = source_cache[other['id']]
            pa = prints.get(a['id'])
            if pa is None:
                continue
            names = rom_renames(syms_a, a['matched_source'], pa, pb, a['name'], b['name'], syms_b)
            if names is None:
                continue
            header_b = net._context(proj['id'], b['unit'])['header']
            prelude = _declared(header_a, header_b, names, a['name'])
            source = apply(a['matched_source'], names)
            for variant in [source, *literal_variants(source, a, b, bool(proj['big_endian']))]:
                if job.stop.is_set() or attempted >= MAX_ATTEMPTS or time.monotonic() >= deadline:
                    return matched
                current = net.store.one('SELECT status FROM functions WHERE id=?', b['id'])
                if current is None or current['status'] != 'open':
                    break
                candidate = prelude + variant
                if twins._missed(b['id'], candidate):
                    continue
                job.cross_candidates += 1
                job.cross_tried += 1
                attempted += 1
                outcome, why = twins._attempt_twin(agent, proj, a, b, candidate,
                    origin=f" in {other['name']}", similar=True)
                reason = f'similar code ({score:.0%}); ' + (why or outcome)
                with net.store.tx() as db:
                    db.execute('INSERT INTO cross_game_attempts(project_id,function_id,source_project_id,'
                               'source_function_id,outcome,reason,created_at) VALUES(?,?,?,?,?,?,?)',
                               (proj['id'], b['id'], other['id'], a['id'], outcome, reason, time.time()))
                    if outcome == 'miss':
                        db.execute('INSERT OR REPLACE INTO twin_misses(function_id,source_hash,project_id,at,why) '
                                   'VALUES(?,?,?,?,?)',
                                   (b['id'], twins._miss_key(candidate), proj['id'], time.time(), reason))
                if outcome == 'match':
                    matched += 1
                    job.cross_matched += 1
                    break
            if net.store.one('SELECT status FROM functions WHERE id=?', b['id'])['status'] == 'matched':
                break
    return matched
