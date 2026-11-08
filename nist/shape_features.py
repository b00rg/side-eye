"""Shape-over-time inputs: what the signal DOES, not how strong it is.

Session 1 showed Bluetooth signal strength can't judge distance in a real room, but the shape of the
signal over time separates behaviours: a linger rises and then holds steady; a walk-past rises and
falls; standing far away, a bag or a blocked path stay flat whatever their level.

For each per-second row of a walk (only past rows are used, as the live device would):
  rise    how much stronger the smoothed signal is than its lowest point in the last LOW_WINDOW rows
  drop    how much weaker it is than its highest point in the last LOW_WINDOW rows
  steady  standard deviation of the smoothed signal over the last STEADY_WINDOW rows
  held    consecutive rows the signal has stayed >= RISE_DB above that recent low
Settings fixed in docs/PREREGISTRATION.md, Amendment 4.
"""
import pandas as pd

LOW_WINDOW, STEADY_WINDOW, RISE_DB = 20, 8, 4.0
SHAPE = ["rise", "drop", "steady", "held"]


def add_shape(walk, low_window=LOW_WINDOW, steady_window=STEADY_WINDOW, rise_db=RISE_DB):
    """walk: rows of ONE walk (Pi CSV columns). Returns a copy with the shape columns, in time order."""
    g = walk.sort_values("t").copy()
    m = g.mean_rssi
    g["rise"] = m - m.rolling(low_window, min_periods=1).min()
    g["drop"] = m.rolling(low_window, min_periods=1).max() - m
    g["steady"] = m.rolling(steady_window, min_periods=2).std().fillna(0.0)
    up = (g["rise"] >= rise_db).astype(int)
    g["held"] = up.groupby((up == 0).cumsum()).cumsum()
    return g


def add_shape_all(pool, **kw):
    """pool: rows of many walks with a `walk` column (compare_models.load_real)."""
    return pd.concat(add_shape(g, **kw) for _, g in pool.groupby("walk", sort=False))
