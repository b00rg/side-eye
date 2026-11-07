"""Ablations and learning curve for the public-data models (exploratory, docs/PREREGISTRATION.md).

Usage (from the repo root, once the real walks and settings.json exist):
  python3 nist/ablations.py --mitre data/external/mitre_rssi.csv --settings settings.json \\
      --real data/runs --cutoff <threshold_dbm> --rate <Pi packets/s> [--seeds 0,1,2] [--auc]

Every variant is scored on the same real walks with the same folds as compare_models.py.
For each it reports Models 2 and 3 (walks correct /N with 95% Wilson interval) and the
pre-registered H1 test (Model 3 vs Model 1). Model 1 and the Rule don't use simulated data,
so they are the same in every row and are printed once.

Variants
  learning curve   number of simulated walks: 50, 100, 300, 1000
  weighting        sim weight in Model 3: 0.1, 0.2, 0.5, 1.0
  losses           obstruction losses: measured (NIST/VSC) vs the old guessed constants
  noise            MITRE jitter replay vs random (Gaussian) noise of the same spread
--seeds repeats each simulated variant with several simulation seeds; --auc adds the
fake-vs-real AUC (nist/sim_vs_real.py) for each variant.
"""
import argparse
import os
import subprocess
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from compare_models import (MODELS, floats, load_real, load_sim, mcnemar, outcomes,  # noqa: E402
                            score_folds, score_walks, wilson)

BASE = dict(n=300, weight=0.2, losses="measured", noise="mitre")
VARIANTS = (
    [("learning curve", dict(BASE, n=n)) for n in (50, 100, 300, 1000)]
    + [("weighting", dict(BASE, weight=w)) for w in (0.1, 0.5, 1.0)]
    + [("losses", dict(BASE, losses="guessed"))]
    + [("noise", dict(BASE, noise="gaussian"))]
)


def sim_dir(v, seed, root):
    return os.path.join(root, f"n{v['n']}_{v['losses']}_{v['noise']}_s{seed}")


def ensure_sim(v, seed, args):
    out = sim_dir(v, seed, args.work)
    if not os.path.exists(os.path.join(out, "manifest.csv")):
        cmd = [sys.executable, os.path.join(HERE, "simulate_walks.py"), args.mitre, args.settings, out,
               "--n", str(v["n"]), "--rate", str(args.rate), "--seed", str(seed), "--noise", v["noise"]]
        if v["losses"] == "guessed":
            cmd += ["--losses", os.path.join(out, "no_such_file.json")]   # forces the fallback constants
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return out


def fmt(k, n):
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({lo:.0%}-{hi:.0%})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mitre", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--real", required=True)
    ap.add_argument("--cutoff", type=float, required=True)
    ap.add_argument("--rate", type=float, default=8)
    ap.add_argument("--seeds", type=floats, default=[0])
    ap.add_argument("--auc", action="store_true", help="also compute the fake-vs-real AUC per variant")
    ap.add_argument("--work", default=os.path.join(HERE, "sim_ablations"), help="where simulated walks go (gitignored)")
    args = ap.parse_args()
    if args.auc:
        from sim_vs_real import heldout_auc

    test_pool = load_real(args.real)
    rows, fixed = [], None
    for group, v in VARIANTS:
        for seed in map(int, args.seeds):
            sim = load_sim(ensure_sim(v, seed, args), args.cutoff)
            scored = score_folds(sim, test_pool, v["weight"])
            o = {name: outcomes(scored, name) for name in MODELS}
            if fixed is None:
                rule = {w: c for f in scored for w, c in score_walks(f, rule_wait=3)["correct"].items()}
                fixed = {"Rule (3 s)": rule, MODELS[0]: o[MODELS[0]]}
            b, c, p = mcnemar(o[MODELS[2]], o[MODELS[0]])
            row = dict(group=group, n_sim=v["n"], sim_weight=v["weight"], losses=v["losses"], noise=v["noise"],
                       seed=seed, model2_correct=sum(o[MODELS[1]].values()), model3_correct=sum(o[MODELS[2]].values()),
                       n_walks=len(o[MODELS[2]]), h1_b=b, h1_c=c, h1_p=round(p, 3))
            if args.auc:
                both = pd.concat([sim.assign(is_sim=1), test_pool.assign(is_sim=0)], ignore_index=True)
                row["sim_vs_real_auc"] = round(heldout_auc(both), 2)
            rows.append(row)
            print(f"done: {group:15s} n={v['n']:<5} w={v['weight']:<4} {v['losses']:9s} {v['noise']:9s} seed {seed}", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv("ablations.csv", index=False)
    n = df.n_walks.iloc[0]
    print("\nSame in every variant (no simulated data):")
    for name, oc in fixed.items():
        print(f"  {name:20s} {fmt(sum(oc.values()), len(oc))}")

    keys = ["group", "n_sim", "sim_weight", "losses", "noise"]
    agg = df.groupby(keys, sort=False).agg(
        model2=("model2_correct", "mean"), model3=("model3_correct", "mean"),
        h1_p=("h1_p", "median"), seeds=("seed", "count"),
        **({"auc": ("sim_vs_real_auc", "mean")} if args.auc else {})).reset_index()
    for col in ("model2", "model3"):
        agg[col] = [fmt(int(round(x)), n) if s == 1 else f"{x:.1f}/{n}" for x, s in zip(agg[col], agg.seeds)]
    print(f"\nModels 2 and 3 under each variant (walks correct out of {n}; "
          f"{'mean over seeds' if len(args.seeds) > 1 else '95% Wilson interval'}; h1_p = Model 3 vs Model 1, McNemar)")
    print(agg.to_string(index=False))
    print("\nSaved ablations.csv (one row per variant and seed). Exploratory: not part of the pre-registered tests.")


if __name__ == "__main__":
    main()
