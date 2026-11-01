"""Function handoffs belong to a helper session; score reductions are measured, never parsed from prose."""
import time


def session_for(db, project, agent, at):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='helper_sessions'").fetchone():
        return None
    return db.execute('SELECT s.* FROM helper_sessions s JOIN projects p ON p.slug=s.project '
                      'JOIN agents a ON a.name=s.agent WHERE p.id=? AND a.id=? AND s.started_at<=? '
                      'AND (s.ended_at IS NULL OR s.ended_at>=?) ORDER BY s.id DESC LIMIT 1',
                      (project, agent, at, at)).fetchone()


def scores(db, function, agent, session):
    first = db.execute('SELECT MIN(created_at) FROM attempts WHERE function_id=? AND agent_id=? '
                       'AND created_at>=? AND created_at<=?',
                       (function, agent, session['started_at'], session['ended_at'] or time.time())).fetchone()[0]
    if first is None:
        return None, None
    before = db.execute('SELECT MIN(score) FROM attempts WHERE function_id=? AND created_at<?',
                        (function, first)).fetchone()[0]
    after = db.execute('SELECT MIN(score) FROM attempts WHERE function_id=? AND agent_id=? '
                       'AND created_at>=? AND created_at<=?',
                       (function, agent, session['started_at'], session['ended_at'] or time.time())).fetchone()[0]
    return before, min(before, after) if before is not None and after is not None else after



def save(db, project, function, agent, text, at=None):
    at = time.time() if at is None else at
    session = session_for(db, project, agent, at) if function else None
    before, after = scores(db, function, agent, session) if session else (None, None)
    previous = db.execute('SELECT id,text FROM notes WHERE function_id=? AND agent_id=? AND session_id=?',
                          (function, agent, session['id'])).fetchone() if session else None
    if previous:
        combined = previous['text'] if text in previous['text'] else previous['text'] + '\n\n' + text
        db.execute('UPDATE notes SET text=?,score_before=?,score_after=? WHERE id=?',
                   (combined, before, after, previous['id']))
    else:
        db.execute('INSERT INTO notes(project_id,function_id,agent_id,text,created_at,session_id,score_before,score_after) '
                   'VALUES(?,?,?,?,?,?,?,?)', (project, function, agent, text, at,
                                              session['id'] if session else None, before, after))


def grouped(store, rows, project):
    result, groups = [], {}
    with store.lock:
        db = store.conn
        for raw in rows:
            row = dict(raw)
            session = session_for(db, project, row['agent_id'], row['created_at'])
            key = (row['agent_id'], row.get('session_id') or (session['id'] if session else None))
            # Exact imported timestamps can identify a recording batch, but never imply a known session.
            if key[1] is None or row['agent'] == 'you':
                key = ('legacy', row['agent_id'], row['created_at']) if row['agent_id'] is not None and row['agent'] != 'you' else ('note', row['id'])
            row['ids'] = [row['id']]
            if session and row.get('score_before') is None:
                row['score_before'], row['score_after'] = scores(db, row['function_id'], row['agent_id'], session)
            if key in groups:
                target = groups[key]
                target['ids'].append(row['id'])
                if row['text'] not in target['text']:
                    target['text'] = row['text'] + '\n\n' + target['text']
            else:
                groups[key] = row
                result.append(row)
    return result


def finish(db, session_id):
    session = db.execute('SELECT * FROM helper_sessions WHERE id=?', (session_id,)).fetchone()
    for note in db.execute('SELECT id,function_id,agent_id FROM notes WHERE session_id=?', (session_id,)).fetchall():
        before, after = scores(db, note['function_id'], note['agent_id'], session)
        db.execute('UPDATE notes SET score_before=?,score_after=? WHERE id=?', (before, after, note['id']))
