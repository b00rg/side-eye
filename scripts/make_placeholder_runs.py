"""Make 24 placeholder walks in the Pi's exact CSV format, so the results pipeline runs before the
real walks exist. Writes them where the real ones go, so swapping later is just replacing files.

Usage (from the repo root, after scripts/get_mitre.sh):
  python3 scripts/make_placeholder_runs.py data/external/mitre_rssi.csv

Writes data/runs/<time>-<type>.csv (8 linger, 8 passer, 4 far, 2 bag, 2 blocked, in the run-sheet
order), settings.json at the repo root, and an empty data/runs/PLACEHOLDER marker. While that marker
exists, compare_models.py warns that the walks are placeholders. Walks come from
nist/simulate_walks.py at 1.35 readings/s (the scan check's rate), turned into rows by
pi/canary_core.Tracker as `canary.py record` would. A walk the Pi would refuse to save is redrawn.

Swapping in the real walks: delete everything in data/runs/ (including PLACEHOLDER), copy the
real CSVs in, and replace settings.json with the Pi's.
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
NEAR, FAR = -48.0, -58.0      # placeholder 2 m / 6 m medians (scan check read about -40 dBm at ~1 m)
RATE, SEED = 1.35, 7
START_CLOCK = 19 * 3600       # placeholder session starts 19:00:00
START_MONO = 5000.0           # canary.py writes time.monotonic(); any start value works


def main():
    mitre_csv = sys.argv[1]
    runs = os.path.join(ROOT, "data", "runs")
    os.makedirs(runs, exist_ok=True)
    cutoff = round((NEAR + FAR) / 2, 1)
    with open(os.path.join(ROOT, "settings.json"), "w") as f:
        json.dump({"threshold_dbm": cutoff, "near_median": NEAR, "far_median": FAR}, f, indent=2)
        f.write("\n")
    open(os.path.join(runs, "PLACEHOLDER"), "w").close()

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
    print(f"Wrote {len(ORDER)} placeholder walks to {runs} {counts}; cutoff {cutoff} dBm; "
          f"{redraws} walks redrawn because the Pi would have refused them")


if __name__ == "__main__":
    main()
