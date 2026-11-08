"""Train the shape model on the Pi (no pandas) and save shape_model.joblib next to settings.json.

Usage (on the Pi, in the code folder):
  python3 train_shape.py ~/data/runs_session1 ~/data/runs        # every walk folder to learn from

Each walk CSV (as written by `canary.py record`) is read in time order and its per-second smoothed
signal (mean_rssi) is turned into shape inputs by shape_core.ShapeTracker, exactly as shape_live.py
does live. Same forest as everywhere else (train_model.model_factory).
"""
import csv
import glob
import os
import sys
from pathlib import Path

import joblib

from canary_core import project_root
from shape_core import SHAPE, ShapeTracker
from train_model import model_factory


def walk_rows(folder):
    x, y, walks, positives = [], [], 0, 0
    for path in sorted(glob.glob(os.path.join(os.path.expanduser(folder), "*.csv"))):
        with open(path, newline="") as f:
            rows = sorted(csv.DictReader(f), key=lambda r: float(r["t"]))
        tracker = ShapeTracker()
        for r in rows:
            x.append(tracker.update(float(r["mean_rssi"])))
            y.append(int(r["label"]))
        walks += 1
        positives += any(int(r["label"]) for r in rows)
    return x, y, walks, positives


def main():
    folders = sys.argv[1:]
    if not folders:
        sys.exit("usage: python3 train_shape.py <walk folder> [<walk folder> ...]")
    x, y = [], []
    for folder in folders:
        fx, fy, walks, lingers = walk_rows(folder)
        print(f"{folder}: {walks} walks ({lingers} lingers), {len(fx)} rows")
        x += fx
        y += fy
    if len(set(y)) < 2:
        sys.exit("Need both linger and non-linger rows to train.")
    model = model_factory().fit(x, y)
    model.feature_names_shape = SHAPE
    out = project_root(__file__) / "shape_model.joblib"
    joblib.dump(model, out)
    print(f"Trained shape model on {len(x)} rows ({sum(y)} lingering) -> {out}")


if __name__ == "__main__":
    main()
