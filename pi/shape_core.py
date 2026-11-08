"""Shape-over-time inputs computed live, one row per second (no pandas: runs on the Pi).

Must match nist/shape_features.py exactly (checked by pi/tests/test_shape_core.py):
  rise    smoothed signal minus its minimum over the last LOW_WINDOW rows (including this one)
  drop    maximum over the last LOW_WINDOW rows minus the smoothed signal
  steady  sample standard deviation (n-1) over the last STEADY_WINDOW rows; 0 until 2 rows
  held    consecutive rows (including this one) with rise >= RISE_DB; 0 when not
"""
import statistics
from collections import deque

LOW_WINDOW, STEADY_WINDOW, RISE_DB = 20, 8, 4.0
SHAPE = ("rise", "drop", "steady", "held")


class ShapeTracker:
    def __init__(self, low_window=LOW_WINDOW, steady_window=STEADY_WINDOW, rise_db=RISE_DB):
        self.low = deque(maxlen=low_window)
        self.steady = deque(maxlen=steady_window)
        self.rise_db = rise_db
        self.held = 0

    def update(self, mean_rssi):
        """Add one per-second smoothed value; returns [rise, drop, steady, held]."""
        self.low.append(mean_rssi)
        self.steady.append(mean_rssi)
        rise = mean_rssi - min(self.low)
        drop = max(self.low) - mean_rssi
        steady = statistics.stdev(self.steady) if len(self.steady) >= 2 else 0.0
        self.held = self.held + 1 if rise >= self.rise_db else 0
        return [rise, drop, steady, float(self.held)]
