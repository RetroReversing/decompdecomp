"""Read-only, opt-in local source seeds. A mapping is a hypothesis; only the ordinary judge is progress.

Paths and mappings live in machine settings, not the portable project's config. No source build script runs.
ROM candidates get a private per-function header only on improvement. Imported projects keep their own build
headers: candidates must compile in that build, so an incompatible reference header is a reported limitation.
"""
import hashlib
import json
import logging
import re
import time
from pathlib import Path

from .. import scoring
from ..forks import csource
from ..network import NetworkError
from ..repair_feedback import trial as repair_trial

log = logging.getLogger('decompdecomp')
MAX_FILES, MAX_BYTES = 512, 16 * 1024 * 1024
MAX_HEADER = 1024 * 1024
_IDENT = r'[A-Za-z_]\w*'
_TOKEN = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|\b' + _IDENT + r'\b', re.S)
_INCLUDE = re.compile(r'^\s*#\s*include\s*[<"]([^>"\n]+)[>"]\s*$', re.M)


def settings(net, proj):
    row = net.store.one('SELECT value FROM settings WHERE key=?', f'source-preparation:{proj["id"]}')
    return json.loads(row['value']) if row else {}


def mappings(text):
    result, targets, aliases = [], set(), {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(rf'\s*({_IDENT})\s*=\s*({_IDENT})\s*@\s*(.+?)\s*', line)
        if not match:
            raise NetworkError('Use one mapping per line: destination = source_symbol @ relative/file.c')
        destination, source, file = match.groups()
        if destination in targets or (source in aliases and aliases[source] != destination):
            raise NetworkError('Each destination and source symbol must have one unambiguous mapping.')
        targets.add(destination)
        aliases[source] = destination
        result.append((destination, source, file))
        if len(result) > MAX_FILES:
            raise NetworkError('Use at most 512 source mappings per project.')
    return result


def overrides(text):
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(rf'\s*({_IDENT})\s*=\s*(-?(?:0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*)\s*', line)
        if not match:
            raise NetworkError('Macro overrides must be numeric, for example OBJECT_LIMIT=128.')
        result[match[1]] = match[2]
    return result


def symbol_aliases(text):
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(rf'\s*({_IDENT})\s*=\s*({_IDENT})\s*', line)
        if not match or match[1] in result:
            raise NetworkError('Use one unambiguous symbol alias per line: reference_name = destination_name.')
        result[match[1]] = match[2]
    return result


def contexts(text):
    """Explicit coherent source/header alternatives, never cross-product SDK revisions."""
    result=[]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        parts=line.split('|')
        if len(parts)!=2 or not parts[1].strip():
            raise NetworkError('Use destination = source_symbol @ relative/file.c | relative/header.h for each context alternative.')
        items=mappings(parts[0])
        if len(items)!=1:raise NetworkError('Each context alternative needs one function mapping.')
        result.append((*items[0],parts[1].strip()))
        if len(result)>16:raise NetworkError('Select at most 16 coherent source/header alternatives.')
    return result


def configure(net, proj, data):
    cfg = {key: str(data.get(key, '')).strip() for key in ('root', 'header', 'include_roots', 'mappings', 'overrides', 'aliases', 'definitions', 'contexts', 'helpers')}
    cfg['enabled'] = bool(data.get('enabled'))
    if any(len(value) > 128_000 for value in cfg.values() if isinstance(value, str)):
        raise NetworkError('Source preparation settings are too large; choose a smaller mapping.')
    from . import source_definitions
    source_definitions.mappings(cfg['definitions'])
    source_definitions.mappings(cfg['helpers'])
    contexts(cfg['contexts'])
    mappings(cfg['mappings'])
    overrides(cfg['overrides'])
    symbol_aliases(cfg['aliases'])
    if cfg['enabled']:
        reader = Reader(cfg)
        for _, _, file in mappings(cfg['mappings']):
            reader.file(reader.root / file)
        for _, file in source_definitions.mappings(cfg['definitions']):
            reader.file(reader.root / file)
        for _, file in source_definitions.mappings(cfg['helpers']):
            reader.file(reader.root / file)
        for _, _, file, header in contexts(cfg['contexts']):
            reader.file(reader.root / file)
            reader.file(reader.root / header)
        if cfg['header']:
            reader.file(Path(cfg['header']).expanduser())
    with net.store.tx() as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                   (f'source-preparation:{proj["id"]}', json.dumps(cfg)))
    return cfg


class Reader:
    """Bound all reads to the explicitly selected roots, including symlink targets and recursive includes."""
    def __init__(self, cfg):
        if not cfg.get('root'):
            raise NetworkError('Choose a local reference source folder first.')
        self.root = Path(cfg['root']).expanduser().resolve()
        self.roots = [self.root] + [Path(p).expanduser().resolve() for p in cfg.get('include_roots', '').splitlines() if p.strip()]
        if not all(p.is_dir() for p in self.roots):
            raise NetworkError('The reference folder and each include root must be existing folders.')
        self.overrides = overrides(cfg.get('overrides', ''))
        self.cache, self.bytes = {}, 0

    def file(self, path):
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.roots):
            raise NetworkError('A reference file leaves the selected source/include folders. Add its folder explicitly.')
        if path.suffix.lower() not in {'.c', '.h', '.cc', '.cpp', '.hpp', '.inc'} or not path.is_file():
            raise NetworkError('Choose an existing C/C++ source or declarations header.')
        if path not in self.cache:
            if len(self.cache) >= MAX_FILES or path.stat().st_size + self.bytes > MAX_BYTES:
                raise NetworkError('Reference reads exceed 512 files / 16 MB. Choose a smaller source subset.')
            text = path.read_bytes()
            self.bytes += len(text)
            self.cache[path] = text.decode('utf-8', errors='replace').replace('\r\n', '\n')
        return self.cache[path]

    def flatten(self, path, seen=None):
        seen = set() if seen is None else seen
        path = path.resolve()
        if path in seen:
            return ''  # guards/cycles do not warrant an unbounded include walk
        if len(seen) >= 64:
            raise NetworkError('Reference includes exceed 64 levels; supply a prepared declarations header.')
        seen.add(path)
        text = self.file(path)
        def include(match):
            name = match[1]
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise NetworkError('Use relative includes within the selected reference folders.')
            candidates = [path.parent / name] + [root / name for root in self.roots]
            found = next((p for p in candidates if p.is_file()), None)
            if found is None:
                raise NetworkError(f'Include {name!r} was not found. Add its SDK folder as an include root or supply a prepared declarations header.')
            return self.flatten(found, seen)
        text = _INCLUDE.sub(include, text)
        # Apply retail values where they are defined, before any dependent struct declarations.
        for name, value in self.overrides.items():
            pattern = re.compile(r'^(\s*#\s*define\s+' + re.escape(name) + r')[ \t]+(?:[^\n\\]|\\\n)*', re.M)
            text = pattern.sub(lambda m, value=value: m[1] + ' ' + value, text)
        if len(text.encode()) > MAX_HEADER:
            raise NetworkError('Flattened declarations exceed 1 MB; supply a smaller declarations header.')
        seen.remove(path)
        return text


def rename(text, aliases):
    return _TOKEN.sub(lambda m: aliases.get(m[0], m[0]), text)


def declarations(text):
    """Remove function bodies without collapsing their original line breaks. This is deliberately conservative."""
    clean, out, start, depth, at = csource._blank(text), [], None, 0, 0
    for i, char in enumerate(clean):
        if char == '{':
            if depth == 0 and clean[:i].rstrip().endswith(')'):
                start = i
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0 and start is not None:
                out.extend((text[at:start], ';' + re.sub(r'[^\n]', ' ', text[start + 1:i + 1])))
                at, start = i + 1, None
    return ''.join(out) + text[at:]


def prepare(reader, cfg, mapping, aliases, fallback):
    destination, symbol, file = mapping
    path = reader.root / file
    original = reader.file(path)
    from . import source_conditions, source_definitions
    initial = reader.overrides
    if cfg.get('header'):
        header, macros = source_conditions.select(reader.flatten(Path(cfg['header']).expanduser()), initial, reader.overrides)
    elif '#include' in original:
        flattened, macros = source_conditions.select(reader.flatten(path.resolve()), initial, reader.overrides)
        header = declarations(flattened)
    else:
        header = fallback
        _, macros = source_conditions.select(fallback, initial, reader.overrides)
    original, _ = source_conditions.select(original, macros, reader.overrides)
    span = csource.find(original, symbol)
    if not span or csource.is_stub(original[span[0]:span[1]]):
        raise NetworkError(f'No C definition of {symbol!r} in its mapped file. Check the mapping.')
    if csource.find(original[span[1]:], symbol):
        raise NetworkError(f'More than one definition of {symbol!r} is present. Supply a source subset with the intended version.')
    # Keep the exact body formatting and its original line number, including __LINE__ uses.
    body = original[span[0]:span[1]]
    # An unused static definition can disappear entirely when compiled alone. Expose only this mapped
    # entry point; retain columns/line breaks and let the normal destination judge settle the result.
    clean = csource._blank(body)
    name_at = clean.find(symbol)
    if name_at >= 0:
        for match in reversed(list(re.finditer(r'\bstatic\b', clean[:name_at]))):
            body = body[:match.start()] + ' ' * len(match[0]) + body[match.end():]
    source = f'#line {original[:span[0]].count(chr(10)) + 1} "reference.c"\n' + rename(body, aliases)
    from .source_type_context import referenced
    header += '\n' + referenced(original, body, header)
    from .source_context import helpers, prototypes
    header += helpers(reader, cfg, body, header, macros)
    header += prototypes(original, body, header, symbol)
    selected = []
    used=set(re.findall(_IDENT,source))
    # Follow only macros reached by this candidate, rather than pulling every header global in.
    for _ in range(8):
        before=len(used)
        for macro in re.finditer(r'^\s*#\s*define\s+('+_IDENT+r')\b([^\n]*)',header,re.M):
            if macro[1] in used:used.update(re.findall(_IDENT,macro[2]))
        if len(used)==before:break
    for name, file in source_definitions.mappings(cfg.get('definitions', '')):
        if name not in used and aliases.get(name,name) not in used:
            continue
        text, _ = source_conditions.select(reader.flatten(reader.root / file), macros, reader.overrides)
        definition = source_definitions.extract(declarations(text), name)
        selected.append(rename(definition, aliases))
    if selected:
        source = '\n'.join(selected) + '\n' + source
    return source, rename(header, aliases)


def prepare_variants(reader, cfg, mapping, aliases, fallback):
    """Prefer authorized definitions, then try the extern-only context under the same budget."""
    result = [prepare(reader, cfg, mapping, aliases, fallback)]
    if cfg.get('definitions'):
        plain = prepare(reader, {**cfg, 'definitions': ''}, mapping, aliases, fallback)
        if plain not in result:result.append(plain)
    # Try at most two explicitly paired alternatives; never mix one SDK's body with
    # another SDK's header. Each costs the same shared compile/time allowance.
    count=0
    for dest, symbol, file, header in contexts(cfg.get('contexts', '')):
        if dest!=mapping[0]:continue
        variant=prepare(reader, {**cfg, 'header': str(reader.root/header)}, (dest,symbol,file), aliases, fallback)
        if variant not in result:result.append(variant)
        count+=1
        if count>=2:break
    return result


def inputs(net, proj, fn):
    """The destination state that a private-header compile was judged against, checked again before promotion."""
    ctx = net._context(proj['id'], fn['unit'])
    symbols = [tuple(r) for r in net.store.all('SELECT name,address,size,kind FROM symbols WHERE project_id=? ORDER BY name', proj['id'])]
    return (proj['config'], fn['unit'], fn['target'], fn['relocs'], fn['vram'], fn['best_source'], ctx['version'], symbols)


def promote_context(net, db, fn, expected, header, agent):
    """Called inside _record's transaction, after an ordinary successful compile/link, before writing the attempt."""
    proj = db.execute('SELECT * FROM projects WHERE id=?', (fn['project_id'],)).fetchone()
    claim = db.execute('SELECT agent_id,expires_at FROM claims WHERE function_id=?', (fn['id'],)).fetchone()
    if not claim or claim['agent_id'] != agent.id or claim['expires_at'] <= time.time():
        raise NetworkError('The function claim changed during preparation. Run the pass again.')
    if fn['status'] != 'open' or inputs(net, proj, fn) != expected:
        raise NetworkError('The function, header or build inputs changed during preparation. Run the pass again.')
    version = net._next_version(db, proj['id'])
    unit = f'local_source_{fn["id"]}'
    db.execute('INSERT INTO context_versions(project_id,version,header,agent_id,message,created_at,unit) VALUES(?,?,?,?,?,?,?)',
               (proj['id'], version, header, agent.id, 'Judge-verified local reference declarations', time.time(), unit))
    db.execute('UPDATE functions SET unit=?, permuted=0 WHERE id=?', (unit, fn['id']))
    net._event(db, proj['id'], agent.id, 'context', f'Local reference declarations for {fn["name"]}')
    return version


def attempt(permuter, proj, fn, source, header, agent, fork, diagnostic=None):
    net = permuter.net
    if fork:
        # The existing build must supply its own compatible types. Never edit its build setup or headers.
        return net.forks.attempt(agent, fork, fn['name'], source, keep=False, quiet=fn['best_score']).get('score')
    expected = inputs(net, proj, fn)
    result = net._compile(proj, header, source, fn)
    if not result.ok:
        from .preparation_diagnostics import compile_error
        raise NetworkError('Prepared C did not compile: ' + compile_error(net, fn, result.log))
    from .source_context import compiler_hints
    for hint in compiler_hints(result.log):
        log.warning('source preparation %s: %s', fn['name'], hint)
        if diagnostic:diagnostic(fn['name'] + ': ' + hint)
    from ..rom.export import _single_definition
    if net.is_rom(proj) and not _single_definition(result.obj, fn['name']):
        raise NetworkError('Prepared declarations emitted extra function bodies. Supply a declarations-only header.')
    candidate, verdict, problems = net._evaluate(fn, proj, result.obj)
    if candidate is None:
        raise NetworkError('Prepared C could not be linked: ' + '; '.join(problems)[:1200])
    diff = scoring.diff(net._target_obj(fn, proj), candidate)
    if verdict is not None:
        diff = scoring.DiffResult(0 if verdict else max(1, diff.score), diff.max_score, diff.rows, verdict)
    score = 0 if diff.bytes_match else max(1, diff.score)
    if fn['best_score'] is None or score < fn['best_score']:
        net._record(fn, agent, score, source, 0, matched=diff.bytes_match, via='local-source',
                    effective_settings=getattr(result, 'effective_settings', None),
                    prepared_header=header, prepared_inputs=expected)
        net.keep_diff(agent.id, fn['id'], diff.rows)
    return score


def run(permuter, proj, st, log_line=lambda line: None, budget=8, seconds=20, only=None):
    """A bounded, cached preparation round before ordinary deterministic/random permutations."""
    net = permuter.net
    cfg = settings(net, proj)
    report = {'tested': 0, 'improved': 0, 'matched': 0, 'errors': []}
    if not cfg.get('enabled') or proj['slug'] in net.links:
        return report
    try:
        reader = Reader(cfg)
        mapped = mappings(cfg.get('mappings', ''))
        aliases = symbol_aliases(cfg.get('aliases', ''))
        for dest, source, _ in mapped:
            if source in aliases and aliases[source] != dest:
                raise NetworkError('A function mapping conflicts with a symbol alias. Keep one destination per source symbol.')
            aliases[source] = dest
        agent, _ = net.local_agent('autopilot')
        fork = net.fork_slug(proj['slug'])
        deadline = time.monotonic() + seconds
        for mapping in mapped:
            if only is not None and mapping[0] not in only:
                continue
            if st.stop.is_set() or report['tested'] >= budget or time.monotonic() >= deadline:
                break
            fn = net.store.one("SELECT * FROM functions WHERE project_id=? AND name=? AND status='open'", proj['id'], mapping[0])
            if not fn or not permuter._hold(agent, fn, count_turn=False, seconds=seconds):
                continue
            st.holding(fn['name'], True)
            cache_key = None
            improved_source = False
            try:
                fresh = net.project_row(proj['slug'])
                fn = net.store.one('SELECT * FROM functions WHERE id=?', fn['id'])
                if fn['status'] != 'open':
                    continue
                fallback = net.forks.permuter_header(fork, fn['name']) if fork else net._context(proj['id'], fn['unit'])['header']
                variants = prepare_variants(reader, cfg, mapping, aliases, fallback)
                retried = False
                for source, header in variants:
                    from .source_context import context_hints
                    for hint in context_hints(source, header):log_line(fn['name'] + ': ' + hint)
                    if st.stop.is_set() or report['tested'] >= budget or time.monotonic() >= deadline:
                        break
                    fresh = net.project_row(proj['slug'])
                    fn = net.store.one('SELECT * FROM functions WHERE id=?', fn['id'])
                    if fn['status'] != 'open':
                        break
                    # Imported build files can change independently: retry on a new explicit pass/session.
                    from .permuter_inputs import build_state
                    state = (st.started_at, fresh['config'], build_state(fresh)) if fork else inputs(net, fresh, fn)
                    digest = hashlib.sha256(repr((state, source, header, cfg)).encode()).hexdigest()
                    cache_key = 'local-source-v1:' + digest
                    if net.store.one('SELECT 1 FROM reuse_trials WHERE function_id=? AND signature=?', fn['id'], cache_key):
                        continue
                    report['tested'] += 1
                    st.add(variants=1, repairs=1)
                    log_line(f'Preparing {fn["name"]} ({report["tested"]}/{budget})')
                    with repair_trial({'kind': 'local source preparation', 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                                       'header_sha256': hashlib.sha256(header.encode()).hexdigest()}):
                        try:
                            score = attempt(permuter, fresh, fn, source, header, agent, fork, log_line)
                        except NetworkError as exc:
                            if (str(exc).startswith('The function, header or build inputs changed') and not fork
                                    and not retried and not st.stop.is_set() and time.monotonic()<deadline
                                    and report['tested']<budget):
                                retried=True
                                variants.append((source,header))
                                log_line(fn['name'] + ': inputs changed; retrying once against fresh destination state.')
                                continue
                            report['errors'].append(f'{fn["name"]}: {exc}')
                            log_line(report['errors'][-1])
                            if not str(exc).startswith(('The function, header or build inputs changed', 'The function claim changed')):
                                with net.store.tx() as db:
                                    db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                                               (fn['id'], cache_key, 'error', str(exc)[:1200]))
                            continue
                    if score is not None and (fn['best_score'] is None or score < fn['best_score']):
                        improved_source = True
                        report['improved'] += 1
                        st.add(improved=1)
                        if score == 0:
                            report['matched'] += 1
                            st.add(matched=1)
                    with net.store.tx() as db:
                        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                                   (fn['id'], cache_key, str(score), 'local source preparation'))
            except (NetworkError, OSError, ValueError) as exc:
                message = f'{fn["name"]}: {exc}'
                report['errors'].append(message)
                log_line(message)
                # Compile failures are cached too, but a changed destination remains retryable.
                if cache_key and not str(exc).startswith(('The function, header or build inputs changed', 'The function claim changed')):
                    with net.store.tx() as db:
                        db.execute('INSERT OR REPLACE INTO reuse_trials(function_id,signature,outcome,reason) VALUES(?,?,?,?)',
                                   (fn['id'], cache_key, 'error', str(exc)[:1200]))
            finally:
                st.holding(fn['name'], False)
                permuter._let_go(agent, fn)
            if improved_source and not fork and hasattr(net, '_comparison_digest'):
                followup(net, proj, fn, st, report, log_line, budget, deadline)
    except (NetworkError, OSError, ValueError) as exc:
        report['errors'].append(str(exc))
        log_line(str(exc))
    for message in report['errors']:
        log.info('permuter preparation: %s', message)
    return report


def followup(net, proj, fn, st, report, log_line, budget, deadline):
    """New prepared context deserves bounded flags before another source mutation; no claim is held here."""
    from .matching_preflight import run as profiles
    remaining=budget-report['tested']
    if remaining<=0 or st.stop.is_set() or time.monotonic()>=deadline:return
    fresh=net.project_row(proj['slug'])
    current=net._function(fn['name'],proj['slug'])
    if current['status']!='open':return
    before=current['best_score']
    result=profiles(net,fresh,current,log_line,
                    lambda:not st.stop.is_set() and time.monotonic()<deadline,
                    max_profiles=min(2,remaining))
    report['tested']+=result['tested'];st.add(variants=result['tested'])
    after=net._function(fn['name'],proj['slug'])
    if before is not None and after['best_score'] is not None and after['best_score']<before:
        report['improved']+=1;st.add(improved=1)
        if after['status']=='matched':report['matched']+=1;st.add(matched=1)
