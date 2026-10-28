"""Per-start resource limits, independent of display metric resets and lifetime billing."""
import math
import threading
import time

from ..network import NetworkError

FIELDS = {'tokens': 10**12, 'seconds': 604800, 'usd': 1000000, 'idle_seconds': 604800}


def validate(values):
    if not isinstance(values, dict) or set(values) - FIELDS.keys():
        raise NetworkError('Use token, elapsed-time, reported-dollar or no-improvement limits.')
    result = {}
    for key, cap in FIELDS.items():
        raw = values.get(key)
        if raw in (None, ''):
            continue
        try:
            value = float(raw)
        except (ValueError, TypeError):
            raise NetworkError('Agent limits must be non-negative numbers.') from None
        if isinstance(raw, bool) or not math.isfinite(value) or not 0 <= value <= cap:
            raise NetworkError(f'{key} must be between 0 and {cap}.')
        if key != 'usd' and not value.is_integer():
            raise NetworkError(f'{key} must be a whole number.')
        if value:
            result[key] = value
    return result


def begin(manager, s):
    s.limit_origin = {'at': time.time(), 'tokens': s.tokens, 'usd': s.usd}
    if s.options.get('limits'):
        threading.Thread(target=watch, args=(manager, s, s.stop, s.limit_origin), daemon=True,
                         name=f'limits-{s.id}').start()


def reason(manager, s, now=None):
    values = s.options.get('limits') or {}
    origin = getattr(s, 'limit_origin', {})
    if not values or not origin:
        return None
    now = time.time() if now is None else now
    for key, used in [('tokens', s.tokens-origin['tokens']), ('usd', s.usd-origin['usd']),
                      ('seconds', now-origin['at'])]:
        if values.get(key) and used >= values[key]:
            return f"agent {key} limit reached ({values[key]:g})"
    if values.get('idle_seconds'):
        last = manager.net.store.one(
            'SELECT MAX(t.created_at) AS t FROM attempts t JOIN agents a ON a.id=t.agent_id '
            'JOIN functions f ON f.id=t.function_id JOIN projects p ON p.id=f.project_id '
            'WHERE p.slug=? AND a.name=? AND t.created_at>=? AND t.compiled AND t.score>=0 '
            'AND t.score<COALESCE((SELECT MIN(old.score) FROM attempts old WHERE old.function_id=t.function_id '
            'AND old.id<t.id AND old.compiled), f.n_insns*100)',s.slug,s.agent_name,origin['at'])['t']
        if now-max(origin['at'],last or 0) >= values['idle_seconds']:
            return 'agent no-improvement time limit reached'
    return None


def enforce(manager, s):
    why = reason(manager, s)
    if why and s.state in ('starting','working','waiting'):
        manager.stop(s.id, by='itself')
        identity=manager.net.store.one('SELECT id FROM agents WHERE name=?',s.agent_name)
        if identity: manager.assignments.pop(identity['id'],None)
        manager._finish(s,why)
        return True
    return False


def watch(manager, s, stopped, origin):
    while not stopped.wait(2):
        if s.stop is not stopped or s.limit_origin is not origin or s.state not in ('starting','working','waiting'):
            return
        if enforce(manager,s):
            return
