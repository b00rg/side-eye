"""Feedback button: turn "someone's lingering now" presses into training rows, stored on the Pi.

What gets stored is anonymous: the four model inputs (mean_rssi, var_rssi, slope, dwell_s), a label,
a weight and the minute it happened. No device address, no raw signal trace.

  - Button press: the last WINDOW_S seconds of the strongest nearby signal become label 1
    (weight 1.0). This also covers lingers the detector missed.
  - Alert that nobody confirms within CONFIRM_S seconds: its last WINDOW_S seconds become label 0
    with a low weight (UNCONFIRMED_WEIGHT), because "nobody pressed" is weak evidence.
  - Alert with a press within CONFIRM_S before or after it: covered by the press (label 1), no
    label 0 added.

pi/train_model.py --feedback feedback.db adds these rows to training.
"""
import sqlite3
import time
from collections import deque

from canary_core import MODEL_COLUMNS

WINDOW_S = 10
CONFIRM_S = 30
UNCONFIRMED_WEIGHT = 0.3

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback_rows (
    id INTEGER PRIMARY KEY,
    minute INTEGER NOT NULL,          -- unix time rounded down to the minute
    source TEXT NOT NULL,             -- 'button' or 'unconfirmed_alert'
    mean_rssi REAL, var_rssi REAL, slope REAL, dwell_s REAL,
    label INTEGER NOT NULL,
    weight REAL NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0 -- 1 once copied to the cloud database
)"""


class FeedbackStore:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.execute(SCHEMA)
        self.db.commit()

    def add(self, vectors, label, weight, source, wall_time=None):
        minute = int((wall_time if wall_time is not None else time.time()) // 60 * 60)
        self.db.executemany(
            "INSERT INTO feedback_rows (minute, source, mean_rssi, var_rssi, slope, dwell_s, label, weight)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(minute, source, *v, label, weight) for v in vectors])
        self.db.commit()
        return len(vectors)

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM feedback_rows").fetchone()[0]

    def rows(self):
        cols = ", ".join(MODEL_COLUMNS)
        return self.db.execute(f"SELECT {cols}, label, weight FROM feedback_rows").fetchall()


def load_rows(path):
    """(x, y, weights) for training, from a feedback.db file."""
    rows = FeedbackStore(path).rows()
    return ([list(r[:4]) for r in rows], [int(r[4]) for r in rows], [float(r[5]) for r in rows])


class Feedback:
    """Live-loop side: remembers recent rows per device and turns presses/alerts into labels."""

    def __init__(self, store, window_s=WINDOW_S, confirm_s=CONFIRM_S):
        self.store, self.window_s, self.confirm_s = store, window_s, confirm_s
        self.recent = {}    # address -> deque of (t, vector, mean_rssi)
        self.pending = []   # (alert_time, vectors) awaiting a press
        self.last_press = None

    def observe(self, now, address, vector):
        q = self.recent.setdefault(address, deque())
        q.append((now, list(vector)))
        while q and q[0][0] < now - self.window_s:
            q.popleft()

    def alert(self, now, address):
        if self.last_press is not None and now - self.last_press <= self.confirm_s:
            return   # someone pressed just before the buzz: already confirmed
        vectors = [v for _, v in self.recent.get(address, ())]
        if vectors:
            self.pending.append((now, vectors))

    def press(self, now):
        """Label the strongest signal's last few seconds as lingering. Returns rows added."""
        self.last_press = now
        live = {a: q for a, q in self.recent.items() if q and q[-1][0] >= now - 2}
        if not live:
            return 0
        address = max(live, key=lambda a: live[a][-1][1][0])   # highest mean_rssi = nearest
        self.pending = [(t, v) for t, v in self.pending if now - t > self.confirm_s]  # confirmed
        return self.store.add([v for _, v in live[address]], 1, 1.0, "button")

    def tick(self, now):
        """Alerts older than confirm_s with no press become weak negatives. Returns rows added."""
        added, keep = 0, []
        for t, vectors in self.pending:
            if now - t > self.confirm_s:
                added += self.store.add(vectors, 0, UNCONFIRMED_WEIGHT, "unconfirmed_alert")
            else:
                keep.append((t, vectors))
        self.pending = keep
        return added
