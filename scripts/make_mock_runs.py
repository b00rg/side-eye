"""Make 24 MOCK walks in the Pi's exact CSV format, so the results pipeline can run before the
real walks exist. Output goes to data/mock/ (never data/runs/).

Usage (from the repo root, after scripts/get_mitre.sh):
  python3 scripts/make_mock_runs.py data/external/mitre_rssi.csv data/mock

Writes data/mock/settings.json (mock calibration) and data/mock/runs/<time>-<type>.csv, one per
walk, in the shuffled recording order from the run sheet: 8 linger, 8 passer, 4 far, 2 bag,
2 blocked. Walks come from nist/simulate_walks.py at 1.35 readings/s (the scan check's rate) and
are turned into rows by pi/canary_core.Tracker, exactly as `canary.py record` would. A walk the
Pi would refuse to save (no rows, or a linger with no positive rows) is drawn again.

MOCK DATA: it comes from our own simulator, so Model 2 will look unrealistically good on it.
Use it to check that everything runs, never as results.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nist"))
from compare_models import ROW_FIELDS, features  # noqa: E402
from simulate_walks import LOSSES, fit_path_loss, load_losses, rssi_mean, simulate  # noqa: E402

ORDER = ["linger", "passer", "linger", "linger", "blocked", "bag", "linger", "passer", "linger", "linger",
         "far", "far", "passer", "passer", "passer", "passer", "passer", "far", "linger", "blocked",
         "bag", "far", "passer", "linger"]
NEAR, FAR = -48.0, -58.0      # mock 2 m / 6 m medians (scan check read about -40 dBm at ~1 m)
RATE, SEED = 1.35, 7
START_CLOCK = 19 * 3600       # mock recording session starts 19:00:00
START_MONO = 5000.0           # canary.py writes time.monotonic(); any start value works


def main():
    mitre_csv, out = sys.argv[1:3]
    runs = os.path.join(out, "runs")
    os.makedirs(runs, exist_ok=True)
    cutoff = round((NEAR + FAR) / 2, 1)
    with open(os.path.join(out, "settings.json"), "w") as f:
        json.dump({"threshold_dbm": cutoff, "near_median": NEAR, "far_median": FAR, "_mock": True}, f, indent=2)

    df = pd.read_csv(mitre_csv)
    df = df[df.tx_android & (df.rssi < 0)]
    A, n, _ = fit_path_loss(df)
    m2, m6 = rssi_mean(2.0, A, n), rssi_mean(6.0, A, n)
    a = (NEAR - FAR) / (m2 - m6)
    b = NEAR - a * m2
    losses, _ = load_losses(LOSSES)
    rng = np.random.default_rng(SEED)

    clock, mono, redraws = START_CLOCK, START_MONO, 0
    for kind in ORDER:
        while True:
            readings, labels, _, _ = simulate(df, A, n, a, b, kind, rng, RATE, losses)
            rows = features(readings, labels, cutoff)
            if len(rows) and (kind != "linger" or rows.label.sum() > 0):
                break
            redraws += 1
        hh, mm, ss = clock // 3600, clock // 60 % 60, clock % 60
        run_id = f"20261107-{hh:02d}{mm:02d}{ss:02d}-000000-{kind}"
        rows = rows.assign(t=(mono + rows.t).round(3))
        out_df = pd.DataFrame({"run_id": run_id, "scenario": kind, "t": rows.t,
                               **{c: rows[c].round(4) for c in ROW_FIELDS}, "label": rows.label})
        out_df.to_csv(os.path.join(runs, f"{run_id}.csv"), index=False)
        clock += 90                       # about one walk every 90 s
        mono += 90 + len(labels)
    counts = pd.Series(ORDER).value_counts().to_dict()
    print(f"Wrote {len(ORDER)} mock walks to {runs} {counts}; mock cutoff {cutoff} dBm; "
          f"{redraws} walks redrawn because the Pi would have refused them")


if __name__ == "__main__":
    main()
