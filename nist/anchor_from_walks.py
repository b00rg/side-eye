"""Anchor the simulation to real recorded walks (pre-registration Amendment 3).

Usage (from the repo root):
  python3 nist/anchor_from_walks.py --walks data/runs_session1 --out nist/sim_anchor.json

Session 1 showed the simulation, anchored to the 2 m / 6 m calibration medians, came out ~8 dB
weaker and about half as noisy as the real walks (fake-vs-real AUC 0.93). This takes the anchors
from the walks themselves instead:
  near: median smoothed signal while a linger walker was stopped (label 1)  -> 2 m
  far:  median smoothed signal during the far walks (standing on the 4 m mark) -> 4 m
  noise_scale: how much to scale MITRE's replayed wobble so simulated var_rssi matches the real
               walks' median var_rssi (found by simulating, measuring, and adjusting twice).
simulate_walks.py --anchor nist/sim_anchor.json then uses these. Never anchor on the walks you
test on: anchor on session 1, test on session 2.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from compare_models import load_sim  # noqa: E402


def sim_var(anchor, cutoff, rate, n=120):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "anchor.json")
        json.dump(anchor, open(path, "w"))
        out = os.path.join(tmp, "sim")
        settings = os.path.join(tmp, "settings.json")
        json.dump({"threshold_dbm": cutoff, "near_median": anchor["near_dbm"], "far_median": anchor["far_dbm"]},
                  open(settings, "w"))
        subprocess.run([sys.executable, "-W", "ignore", os.path.join(HERE, "simulate_walks.py"),
                        os.path.join(ROOT, "data", "external", "mitre_rssi.csv"), settings, out,
                        "--n", str(n), "--rate", str(rate), "--seed", "11", "--anchor", path],
                       check=True, stdout=subprocess.DEVNULL)
        return float(load_sim(out, cutoff).var_rssi.median())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--walks", required=True, help="folder of real walk CSVs (and its settings.json)")
    ap.add_argument("--out", default=os.path.join(HERE, "sim_anchor.json"))
    ap.add_argument("--far-m", type=float, default=4.0, help="distance of the far walks (default 4 m)")
    args = ap.parse_args()
    d = pd.concat(pd.read_csv(p) for p in glob.glob(os.path.join(args.walks, "*.csv")))
    cutoff = json.load(open(os.path.join(args.walks, "settings.json")))["threshold_dbm"]
    rate = round(float(d.packet_rate_hz.median()), 2)
    anchor = dict(near_m=2.0, near_dbm=round(float(d[(d.scenario == "linger") & (d.label == 1)].mean_rssi.median()), 1),
                  far_m=args.far_m, far_dbm=round(float(d[d.scenario == "far"].mean_rssi.median()), 1), noise_scale=1.0)
    real_var = float(d.var_rssi.median())
    for _ in range(2):   # var grows roughly with scale squared; two adjustments get close
        anchor["noise_scale"] = round(anchor["noise_scale"] * (real_var / sim_var(anchor, cutoff, rate)) ** 0.5, 3)
    final_var = sim_var(anchor, cutoff, rate)
    anchor["_source"] = dict(walks=os.path.relpath(args.walks, ROOT), n_walks=int(d.run_id.nunique()), rate=rate,
                             cutoff=cutoff, real_var_rssi=round(real_var, 1), sim_var_rssi=round(final_var, 1))
    json.dump(anchor, open(args.out, "w"), indent=2)
    print(f"2 m (stopped lingers) = {anchor['near_dbm']} dBm, {args.far_m:g} m (far walks) = {anchor['far_dbm']} dBm, "
          f"noise x{anchor['noise_scale']} (var_rssi real {real_var:.1f}, sim {final_var:.1f}) -> {args.out}")


if __name__ == "__main__":
    main()
