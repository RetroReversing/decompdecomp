"""Bounded, function-attributed activity for concurrent deep searches."""
import threading
import time


class Activity:
    def __init__(self, log):
        self.log = log
        self.lock = threading.Lock()
        self.active = {}
        self.recent = []
        self.summary = {}
        self.started = {}

    def update(self, name, value=None, line=None, finished=False):
        with self.lock:
            self.started.setdefault(name, time.monotonic())
            self.recent = [w for w in self.recent if w['function'] != name]
            item = self.active.setdefault(name, {'function': name, 'phase': 'starting', 'tested': 0,
                                                 'improvements': 0, 'lines': []})
            if value:
                item.update(value)
            if line is not None:
                item['lines'] = [*item['lines'], str(line)[-800:]][-6:]
            item['updated_at'] = time.time()
            if finished:
                item['elapsed_seconds'] = max(0, time.monotonic() - self.started.pop(name))
                self.recent = [dict(item), *self.recent][:8]
                self.active.pop(name, None)
            # Publish under the same lock so an older snapshot cannot replace a newer one.
            try:
                self._publish()
            except RuntimeError:
                if not finished:
                    raise

    def publish(self, value):
        with self.lock:
            self.summary.update(value)
            self._publish()

    def _publish(self):
        self.log.progress({**self.summary, 'workers': [dict(w, elapsed_seconds=max(0, time.monotonic() - self.started[name]))
                                                     for name, w in self.active.items()],
                           'recent_workers': list(self.recent)})
