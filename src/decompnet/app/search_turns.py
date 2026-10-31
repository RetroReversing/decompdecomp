"""Project sweep turns use a soft allowance; productive work can reach the user's hard cap.

Explicit single-function runs keep their full requested limit. Rotation never pauses a function:
rotated functions become eligible on the next requested sweep, with all saved candidates intact.
"""
from collections import deque

WINDOW = 32
MIN_SECONDS = 15


class Allowance:
    def __init__(self, seconds, started):
        self.started = started
        self.hard = started+seconds
        self.soft = started+seconds/2
        self.extension = seconds/2
        self.outputs = deque(maxlen=WINDOW)

    def observe(self, improved, output, now):
        if improved:
            self.soft = min(self.hard,max(self.soft,now+self.extension))
            self.outputs.clear()
        elif output:
            self.outputs.append(output)

    def reason(self, now):
        if now < self.started+MIN_SECONDS:
            return ''
        if len(self.outputs) == WINDOW and len(set(self.outputs)) <= WINDOW//5:
            return 'Budget rotation: repeated function output without improvement'
        return 'Budget rotation: soft allowance reached without recent progress' if now >= self.soft else ''
