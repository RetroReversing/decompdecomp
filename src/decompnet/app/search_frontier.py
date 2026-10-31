"""Small source frontier ranked by instruction structure as well as the ordinary numeric score.

This is search guidance only. Displayed rows never prove byte identity, and alternatives do not
replace the saved source. A normal judge must verify any later improvement.
"""
import hashlib

from . import permuter_focus, region_repairs

WIDTH = 6


def structure(rows):
    rows = permuter_focus.normalized(rows)
    if not rows or any(not isinstance(row, (list, tuple)) or len(row) != 3 for row in rows):
        return 1000000000
    return sum(1 if marker in {'r', 's', 'o'} else 3 for marker, _, _ in rows if marker != ' ')


class Frontier:
    def __init__(self):
        self.nodes = []

    def offer(self, source, score, rows):
        """Keep the numeric winner and distinct structural shapes, with bounded source/output counts."""
        if score is None or score <= 0:
            return
        rows = permuter_focus.normalized(rows)
        node = {'source': source, 'score': score, 'fitness': structure(rows),
                'kind': permuter_focus.classify(rows), 'regions': region_repairs.snapshot(rows),
                'output': hashlib.sha256(repr(rows or source).encode()).hexdigest()}
        pool = [n for n in self.nodes if n['source'] != source]+[node]
        numeric = sorted(pool, key=lambda n: (n['score'], n['fitness']))
        structural = sorted(pool, key=lambda n: (n['fitness'], n['score']))
        # Preserve distinct failure shapes: a moved load can be a useful sibling of a register miss.
        selected = numeric[:1]
        for candidate in structural:
            if not any(n['kind'] == candidate['kind'] for n in selected):
                selected.append(candidate)
        for candidate in structural:
            if candidate not in selected:
                selected.append(candidate)
        self.nodes = []
        counts = {}
        for candidate in selected:
            key = candidate['output']
            if counts.get(key, 0) >= 2:
                continue
            counts[key] = counts.get(key, 0)+1
            self.nodes.append(candidate)
            if len(self.nodes) == WIDTH:
                break

    def choose(self, index, fallback):
        return self.nodes[index % len(self.nodes)]['source'] if self.nodes else fallback
