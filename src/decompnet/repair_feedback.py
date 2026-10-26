"""Local evidence for turning successful manual repairs into future deterministic rules.

A judge verdict is evidence of a match; an agent's explanation is a hypothesis, never a new rule.
The shared recorder handles every architecture and both ROM and imported-project modes.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import time

from .network import NetworkError
from .project import compiler_for

CATEGORIES = ('unclassified', 'types', 'casts', 'constants', 'addresses', 'signatures', 'layout',
              'temporaries', 'expression_order', 'control_flow', 'compiler_settings', 'other')
STATES = ('new', 'reviewed', 'implemented', 'dismissed')
SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS repair_attempt_settings(
        attempt_id INTEGER PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE, settings TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS repair_examples(
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        function_id INTEGER NOT NULL REFERENCES functions(id) ON DELETE CASCADE,
        attempt_id INTEGER REFERENCES attempts(id) ON DELETE CASCADE,
        before_attempt_id INTEGER REFERENCES attempts(id) ON DELETE SET NULL, agent_id INTEGER, created_at REAL NOT NULL, evidence TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'unclassified', explanation TEXT NOT NULL DEFAULT '',
        automation TEXT NOT NULL DEFAULT '', review_note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new')''',
    'CREATE INDEX IF NOT EXISTS idx_repair_examples_project ON repair_examples(project_id,id)',
    'CREATE INDEX IF NOT EXISTS idx_repair_examples_attempt ON repair_examples(attempt_id)',
    'CREATE INDEX IF NOT EXISTS idx_repair_examples_function ON repair_examples(function_id,id)',
)


def _settings(proj, fn):
    cfg = json.loads(proj['config'])
    if cfg.get('kind') == 'fork':
        # The build system owns per-file flags/includes; a global compiler name cannot stand in for them.
        return {'mode': 'project', 'platform': cfg.get('platform'), 'declared_compiler': cfg.get('compiler'),
                'version': cfg.get('version'), 'unit': fn['unit'], 'per_file_flags_recorded': False}
    return {'mode': 'rom', 'platform': cfg.get('platform'), 'compiler': compiler_for(cfg, fn['vram']) if cfg.get('compiler') else None,
            'settings_source': 'project configuration at recording; temporary compile overrides may differ',
            'gp': cfg.get('gp'), 'big_endian': bool(proj['big_endian']),
            'rom': {key: cfg.get('rom', {}).get(key) for key in ('sha1', 'game_code', 'region', 'byte_order')}}



class RepairFeedback:
    def __init__(self, net):
        self.net, self.store = net, net.store
        with self.store.tx() as db:
            for statement in SCHEMA:
                db.execute(statement)

    def attempt(self, db, fn, attempt_id, context_version):
        proj = db.execute('SELECT * FROM projects WHERE id=?', (fn['project_id'],)).fetchone()
        settings = _settings(proj, fn)
        header = db.execute('SELECT unit,header FROM context_versions WHERE project_id=? AND version=? '
                            "AND unit IN (?, '') ORDER BY (unit=?) DESC LIMIT 1",
                            (fn['project_id'], context_version, fn['unit'] or '', fn['unit'] or '')).fetchone()
        settings['context_unit'] = header['unit'] if header and settings['mode'] == 'rom' else None
        settings['context_sha256'] = hashlib.sha256(header['header'].encode()).hexdigest() if header and settings['mode'] == 'rom' else None
        db.execute('INSERT INTO repair_attempt_settings VALUES(?,?)', (attempt_id, json.dumps(settings)))

    def capture(self, db, fn, source, agent_id, via, context_version, attempt_id=None):
        if fn['status'] == 'matched' or not source:
            return  # polishing an existing match is not a new matching repair
        proj = db.execute('SELECT * FROM projects WHERE id=?', (fn['project_id'],)).fetchone()
        before = db.execute('SELECT * FROM attempts WHERE function_id=? AND compiled=1 AND score>0 AND source IS NOT NULL '
                            'AND (? IS NULL OR id<?) ORDER BY score,id DESC LIMIT 1',
                            (fn['id'], attempt_id, attempt_id)).fetchone()
        old_settings = db.execute('SELECT settings FROM repair_attempt_settings WHERE attempt_id=?',
                                  (before['id'],)).fetchone() if before else None
        recorded = db.execute('SELECT settings FROM repair_attempt_settings WHERE attempt_id=?',
                              (attempt_id,)).fetchone() if attempt_id else None
        current = json.loads(recorded['settings']) if recorded else _settings(proj, fn)
        meta = json.loads(fn['meta'] or '{}')
        agent = db.execute('SELECT name FROM agents WHERE id=?', (agent_id,)).fetchone() if agent_id else None
        session = None
        if agent and db.execute("SELECT 1 FROM sqlite_master WHERE name='helper_sessions'").fetchone():
            session = db.execute('SELECT id,helper_id,model,kind FROM helper_sessions WHERE agent=? AND project=? '
                                 'AND ended_at IS NULL ORDER BY id DESC LIMIT 1', (agent['name'], proj['slug'])).fetchone()
        old_source = before['source'] if before else None
        evidence = {
            'function': fn['name'], 'function_id': fn['id'], 'unit': fn['unit'],
            'symbol': meta.get('symbol', fn['name']), 'source_path': meta.get('source'),
            'demangled': meta.get('demangled'),
            'agent': {'name': agent['name'] if agent else None, 'session': dict(session) if session else None},
            'size': fn['size'], 'instructions': fn['n_insns'], 'vram': fn['vram'],
            'target_sha256': hashlib.sha256(fn['target']).hexdigest() if fn['target'] else None,
            'fingerprint': fn['fingerprint'], 'via': via, 'target_asm': fn['asm'],
            'judge': 'objdiff 100%' if current['mode'] == 'project' else 'linked byte-identical',
            'after': {'attempt_id': attempt_id, 'source': None if attempt_id else source,
                      'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                      'context_version': context_version, 'settings': current},
            'before': {'attempt_id': before['id'], 'source_sha256': hashlib.sha256(old_source.encode()).hexdigest(),
                       'score': before['score'],
                       'agent_id': before['agent_id'], 'context_version': before['context_version'],
                       'settings': json.loads(old_settings['settings']) if old_settings else None} if before else None,
            'comparison': 'closest saved failed attempt; may involve other edits/settings, not necessarily the final edit',
        }
        db.execute('INSERT INTO repair_examples(project_id,function_id,attempt_id,before_attempt_id,agent_id,created_at,evidence) '
                   'VALUES(?,?,?,?,?,?,?)', (fn['project_id'], fn['id'], attempt_id, before['id'] if before else None,
                                             agent_id, time.time(), json.dumps(evidence)))

    def report(self, agent, slug, function, change, category='other', automation='', case_id=0):
        fn = self.net._function(function, slug)
        if category not in CATEGORIES or not change.strip():
            raise NetworkError('Give a short change explanation and a valid repair category: ' + ', '.join(CATEGORIES))
        with self.store.tx() as db:
            row = db.execute('SELECT id FROM repair_examples WHERE project_id=? AND function_id=? AND agent_id=? '
                             'AND (?=0 OR id=?) ORDER BY id DESC LIMIT 1',
                             (fn['project_id'], fn['id'], agent.id, case_id, case_id)).fetchone()
            if not row:
                raise NetworkError('No captured matching repair by you exists for this function. Match it first.')
            db.execute("UPDATE repair_examples SET explanation=?,category=?,automation=?,status='new' WHERE id=?",
                       (change.strip()[:3000], category, automation.strip()[:3000], row['id']))
        return f"Repair example #{row['id']} annotated for review. This does not add or enable an automatic rule."

    def _where(self, slug='', category='', status='', comparable=False):
        clauses, args = ['1=1'], []
        if slug:
            clauses.append('e.project_id=?'); args.append(self.net._project(slug)['id'])
        if category:
            if category not in CATEGORIES:
                raise NetworkError('Unknown repair category.')
            clauses.append('e.category=?'); args.append(category)
        if status:
            if status not in STATES:
                raise NetworkError('Unknown review state.')
            clauses.append('e.status=?'); args.append(status)
        if comparable:
            clauses.append('e.before_attempt_id IS NOT NULL')
        return ' AND '.join(clauses), args

    def listing(self, slug='', category='', status='', before=0, comparable=False):
        where, args = self._where(slug, category, status, comparable)
        rows = self.store.all('SELECT e.id,e.created_at,e.category,e.explanation,e.automation,e.status,'
                              'p.slug,p.name AS project,f.name AS function,a.name AS agent FROM repair_examples e '
                              'JOIN projects p ON p.id=e.project_id JOIN functions f ON f.id=e.function_id '
                              'LEFT JOIN agents a ON a.id=e.agent_id WHERE ' + where +
                              ' AND (?=0 OR e.id<?) ORDER BY e.id DESC LIMIT 26', *args, before, before)
        groups = self.store.all('SELECT e.category,e.status,COUNT(*) AS count FROM repair_examples e WHERE ' + where +
                                ' GROUP BY e.category,e.status', *args)
        return {'examples': [dict(r) for r in rows[:25]], 'more': len(rows) > 25,
                'groups': [dict(r) for r in groups], 'categories': CATEGORIES, 'states': STATES}

    def detail(self, slug, case_id, include_source=True):
        where, args = self._where(slug)
        row = self.store.one('SELECT e.*,p.slug,p.name AS project,a.name AS agent FROM repair_examples e '
                             'JOIN projects p ON p.id=e.project_id LEFT JOIN agents a ON a.id=e.agent_id '
                             'WHERE ' + where + ' AND e.id=?', *args, case_id)
        if row is None:
            raise NetworkError('No repair example exists in this project.')
        result = dict(row); evidence = json.loads(result.pop('evidence'))
        for stage in ('before', 'after'):
            value = evidence.get(stage)
            if not value:
                continue
            if include_source:
                aid = row['before_attempt_id'] if stage == 'before' else value.get('attempt_id')
                attempt = self.store.one('SELECT source,function_id FROM attempts WHERE id=?', aid) if aid else None
                valid = attempt and attempt['function_id'] == row['function_id'] and hashlib.sha256(
                    (attempt['source'] or '').encode()).hexdigest() == value.get('source_sha256')
                value['source'] = attempt['source'] if valid else value.get('source')
                unit = (value.get('settings') or {}).get('context_unit')
                if unit is not None:
                    header = self.store.one('SELECT header FROM context_versions WHERE project_id=? AND version=? AND unit=?',
                                            row['project_id'], value['context_version'], unit)
                elif evidence['after']['settings']['mode'] == 'rom':
                    # Context versions are unique within a project, including older attempts without settings snapshots.
                    header = self.store.one('SELECT header FROM context_versions WHERE project_id=? AND version=?',
                                            row['project_id'], value['context_version'])
                else:
                    header = None
                expected = (value.get('settings') or {}).get('context_sha256')
                if header and expected and hashlib.sha256(header['header'].encode()).hexdigest() != expected:
                    header = None
                value['header'] = header['header'] if header else None
            else:
                value.pop('source', None)
        if not include_source:
            evidence.pop('target_asm', None)
        if include_source:
            old = (evidence.get('before') or {}).get('source') or ''
            new = evidence['after'].get('source') or ''
            evidence['diff'] = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                                          fromfile='before.c', tofile='matched.c'))
        return result | {'evidence': evidence}

    def review(self, slug, case_id, status, category='', note=None):
        if status not in STATES:
            raise NetworkError('Choose a valid review state.')
        self.detail(slug, case_id, include_source=False)
        if category and category not in CATEGORIES:
            raise NetworkError('Choose a valid repair category.')
        with self.store.tx() as db:
            db.execute("UPDATE repair_examples SET status=?, category=CASE WHEN ?='' THEN category ELSE ? END, "
                       'review_note=COALESCE(?,review_note) WHERE id=?',
                       (status, category, category, str(note)[:6000] if note is not None else None, case_id))
        return {'message': 'Review state saved.'}

    def export(self, slug='', category='', status='', include_source=False, comparable=False):
        where, args = self._where(slug, category, status, comparable)
        maximum = self.store.one('SELECT MAX(e.id) AS id FROM repair_examples e WHERE ' + where, *args)['id'] or 0
        yield '{"schema_version":1,"examples":['
        after, first = 0, True
        while True:
            rows = self.store.all('SELECT e.id FROM repair_examples e JOIN projects p ON p.id=e.project_id WHERE ' +
                                  where + ' AND e.id>? AND e.id<=? ORDER BY e.id LIMIT 50', *args, after, maximum)
            if not rows:
                break
            for row in rows:
                yield ('' if first else ',') + json.dumps(self.detail(slug, row['id'], include_source))
                first, after = False, row['id']
        yield ']}'
