"""Export simulated walks as model rows, so the Pi can train Model 3 without the MITRE data.

Usage (from the repo root):
  python3 nist/export_sim_rows.py --sim nist/sim --cutoff <threshold_dbm> --out data/sim_rows.csv

Rows are computed by pi/canary_core.Tracker (same as the real walks) and hold the four model
inputs plus the label. pi/train_model.py --sim-rows <file> trains Model 3 from them.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_models import FEATS, load_sim  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--cutoff", type=float, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = load_sim(args.sim, args.cutoff)[[*FEATS, "label"]].round(4)
    rows.to_csv(args.out, index=False)
    print(f"Wrote {len(rows)} simulated rows ({rows.label.sum()} positive) to {args.out} "
          f"({os.path.getsize(args.out) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
