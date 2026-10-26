"""Local, paged records of what the app sends to helpers, separate from the short activity feed.

CLI internals (provider system prompts, private reasoning and context compaction) are outside the app.
No environment, command-line authentication or HTTP headers are recorded here.
"""
from __future__ import annotations

import json
import re
import time

from ..network import NetworkError


class Transcripts:
    def __init__(self, store):
        self.store = store
        with store.tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS helper_messages('
                       'id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, created_at REAL NOT NULL, '
                       'kind TEXT NOT NULL, text TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_helper_messages_session ON helper_messages(session_id,id)')

    def append(self, session, kind, value, secrets=()):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        for secret in secrets:
            if secret:
                text = text.replace(secret, '[redacted]')
        # Tool payloads can echo credentials too; do not persist common explicit credential fields.
        text = re.sub(r'(?i)("(?:api_key|access_token|refresh_token|authorization|password)"\s*:\s*)"(?:\\.|[^"\\])*"',
                      r'\1"[redacted]"', text)
        text = re.sub(r'(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [redacted]', text)
        with self.store.tx() as db:
            db.execute('INSERT INTO helper_messages(session_id,created_at,kind,text) VALUES(?,?,?,?)',
                       (session, time.time(), kind, text))

    def read(self, slug, helper, session=0, after=0, before=0):
        sessions = self.store.all('SELECT * FROM helper_sessions WHERE project=? AND helper_id=? AND '
                                  '(?=0 OR id<?) ORDER BY id DESC LIMIT 30', slug, helper, before, before)
        if not session:
            session = sessions[0]['id'] if sessions else 0
        row = self.store.one('SELECT * FROM helper_sessions WHERE id=? AND project=? AND helper_id=?',
                             session, slug, helper)
        if session and row is None:
            raise NetworkError('This session does not belong to this project and helper.')
        events = self.store.all('SELECT * FROM helper_messages WHERE session_id=? AND id>? ORDER BY id LIMIT 26',
                               session, max(0, after)) if row else []
        return {'sessions': [dict(r) for r in sessions], 'session': dict(row) if row else None,
                'events': [dict(r) for r in events[:25]], 'more': len(events) > 25}
