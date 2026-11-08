"""Replay a recorded walk through the trained model, printed like `canary.py live --use-model`.

Usage (from the repo root, after training model.joblib):
  python3 scripts/replay_walk.py data/runs_session2/<walk>.csv [more walks...]

For each second the Pi recorded, shows the signal, the near streak, Model 3's score and what each
driver would do: the model (score >= 0.8 twice within 2.5 s) and the 3-second rule, both with the
live loop's 15 s cooldown. The `truth` column is the label from the recording (linger = 1).
"""
import os
import sys

import joblib
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pi"))
from canary_core import MODEL_COLUMNS, ModelAlert  # noqa: E402

COOLDOWN_S = 15


def replay(path, model):
    d = pd.read_csv(path)
    t0 = d.t.iloc[0]
    alert, last = ModelAlert(), {"model": -1e9, "rule": -1e9}
    scores = model.predict_proba(d[list(MODEL_COLUMNS)].values)[:, 1]
    print(f"\n{os.path.basename(path)}  ({d.scenario.iloc[0]}, {len(d)} rows)")
    print("   time | mean RSSI | near streak | score | truth | model     | rule")
    firsts = {}
    for (_, row), score in zip(d.iterrows(), scores):
        now = row.t - t0
        fired = {}
        for name, trig in (("model", alert.update(now, score)), ("rule", row.close_streak_s >= 3.0)):
            fired[name] = trig and now - last[name] >= COOLDOWN_S
            if fired[name]:
                last[name] = now
                firsts.setdefault(name, now)
        cell = lambda name: "** ALERT" if fired[name] else "watching"
        print(f"  {now:5.0f}s | {row.mean_rssi:7.1f}   | {row.close_streak_s:6.1f} s    | {score:4.2f}  |   {int(row.label)}   "
              f"| {cell('model'):9s} | {cell('rule')}")
    pos = d.index[d.label == 1]
    stop = (d.t.loc[pos[0]] - t0 - 3) if len(pos) else None
    for name in ("model", "rule"):
        t = firsts.get(name)
        if d.scenario.iloc[0] == "linger":
            verdict = ("never buzzed (missed)" if t is None else
                       f"buzzed {t - stop:+.0f} s after the stop" + (" (too early)" if t < stop + 3 else ""))
        else:
            verdict = "stayed quiet (correct)" if t is None else f"buzzed at {t:.0f} s (false alarm)"
        print(f"  {name:5s}: {verdict}")


def main():
    model = joblib.load(os.path.join(ROOT, "model.joblib"))
    for path in sys.argv[1:]:
        replay(path, model)


if __name__ == "__main__":
    main()
