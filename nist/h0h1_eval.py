"""Real-data check: do our models beat a simple threshold on public, real-world Bluetooth data?

Usage (from the repo root):
  git clone --depth 1 https://github.com/mitll/H0H1.git data/external/h0h1
  python3 nist/h0h1_eval.py [--window 10] [--out results/h0h1.png]

MIT Lincoln Laboratory's H0H1 dataset (PACT, CC BY 4.0): pairs of people living together, each with an
iPhone, go about daily life for ~15 minutes either within 6 ft of each other ("close", H1) or 10 ft
or more apart ("far", H0); phones in hands or pockets, sitting or standing, in their own homes.

Task: cut every session into windows of --window seconds; is each window close or far?
  Threshold: "mean RSSI >= X", with X picked on the training sessions to maximise balanced accuracy
             (the best a single cutoff can do, chosen fairly).
  Model:     our random forest (pi/train_model.model_factory: 20 trees, depth 4, balanced), on mean,
             median, spread (var), trend (slope), 10th/90th percentiles and readings per second.
Both are tested on sessions they never saw: grouped 5-fold cross-validation, where the two phones'
logs of the same session stay in the same fold. Scores: balanced accuracy and miss + false-alarm rate
(TC4TL-style, 1.0 = no better than always saying "far"), with 95% intervals from resampling sessions.
This tests near-vs-far on phones, not our linger timing on the Pi.
"""
import argparse
import glob
import os
import re
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pi"))
from train_model import model_factory  # noqa: E402

FEATS = ["mean", "median", "var", "slope", "p10", "p90", "rate"]


def load(root):
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "*", "**", "*.txt"), recursive=True)):
        label = 1 if os.sep + "Close" + os.sep in path else 0
        times, rssi = [], []
        with open(path, errors="ignore") as f:
            for line in f:
                p = line.rstrip("\n").split(",")
                if len(p) >= 5 and p[1] == "Bluetooth" and p[4] == "BlueProxTx":
                    try:
                        r = int(p[3])
                        t = datetime.strptime(p[0], "%Y-%m-%d %H:%M:%S.%f")
                    except ValueError:
                        continue
                    if r < 0:   # 127 = no reading
                        times.append(t)
                        rssi.append(r)
        if len(rssi) > 50:
            rows.append(dict(path=path, label=label, start=times[0], t=np.array([(x - times[0]).total_seconds() for x in times]),
                             rssi=np.array(rssi, float)))
    # the two phones of one session start within minutes of each other: give them one group id
    rows.sort(key=lambda r: r["start"])
    group, last = -1, None
    for r in rows:
        if last is None or (r["start"] - last).total_seconds() > 300 or r["label"] != prev_label:
            group += 1
        r["group"], last, prev_label = group, r["start"], r["label"]
    return rows


def windows(rows, width):
    out = []
    for r in rows:
        for k in range(int(r["t"][-1] // width)):
            m = (r["t"] >= k * width) & (r["t"] < (k + 1) * width)
            if m.sum() < 5:
                continue
            x, y = r["t"][m], r["rssi"][m]
            slope = np.polyfit(x - x.mean(), y, 1)[0] if np.ptp(x) > 0 else 0.0
            out.append(dict(group=r["group"], label=r["label"], mean=y.mean(), median=np.median(y), var=y.var(),
                            slope=slope, p10=np.percentile(y, 10), p90=np.percentile(y, 90), rate=m.sum() / width))
    return pd.DataFrame(out)


def best_cutoff(mean, label):
    """Cutoff on mean RSSI with the best balanced accuracy on these (training) windows."""
    best = (-1, None)
    for c in np.unique(np.round(mean, 1)):
        pred = mean >= c
        ba = 0.5 * (pred[label == 1].mean() + (~pred[label == 0]).mean())
        best = max(best, (ba, c))
    return best[1]


def scores(label, pred):
    hit = pred[label == 1].mean()
    fa = pred[label == 0].mean()
    return dict(balanced_acc=0.5 * (hit + 1 - fa), miss=1 - hit, false_alarm=fa, ndcf=(1 - hit) + fa)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(ROOT, "data", "external", "h0h1"))
    ap.add_argument("--window", type=float, default=10.0)
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "h0h1.png"))
    args = ap.parse_args()
    rows = load(args.root)
    w = windows(rows, args.window)
    groups = w.group.nunique()
    print(f"H0H1: {len(rows)} logs, {groups} sessions ({w.groupby('group').label.first().sum()} close, "
          f"{groups - w.groupby('group').label.first().sum()} far), {len(w)} windows of {args.window:g} s")

    pred = {"threshold": np.zeros(len(w), bool), "model": np.zeros(len(w), bool)}
    prob = np.zeros(len(w))
    for train, test in GroupKFold(n_splits=5).split(w, groups=w.group):
        tr, te = w.iloc[train], w.iloc[test]
        c = best_cutoff(tr["mean"].to_numpy(), tr.label.to_numpy())
        pred["threshold"][test] = te["mean"].to_numpy() >= c
        m = model_factory().fit(tr[FEATS].to_numpy(), tr.label.to_numpy())
        prob[test] = m.predict_proba(te[FEATS].to_numpy())[:, 1]
        pred["model"][test] = prob[test] >= 0.5

    label = w.label.to_numpy()
    rng = np.random.default_rng(0)
    ids = w.group.unique()
    print(f"\n{'':10s} {'balanced acc':>13s} {'95% CI':>14s} {'miss':>6s} {'false alarm':>12s} {'miss+FA':>8s}")
    result = {}
    for name, p in pred.items():
        s = scores(label, p)
        boot = []
        for _ in range(1000):   # resample whole sessions
            pick = rng.choice(ids, len(ids))
            idx = np.concatenate([np.flatnonzero(w.group.to_numpy() == g) for g in pick])
            if label[idx].min() == label[idx].max():
                continue
            boot.append(scores(label[idx], p[idx])["balanced_acc"])
        lo, hi = np.percentile(boot, [2.5, 97.5])
        result[name] = (s, lo, hi)
        print(f"{name:10s} {s['balanced_acc']:13.0%} {f'{lo:.0%}-{hi:.0%}':>14s} {s['miss']:6.0%} "
              f"{s['false_alarm']:12.0%} {s['ndcf']:8.2f}")
    diff = []
    for _ in range(1000):
        pick = rng.choice(ids, len(ids))
        idx = np.concatenate([np.flatnonzero(w.group.to_numpy() == g) for g in pick])
        if label[idx].min() != label[idx].max():
            diff.append(scores(label[idx], pred["model"][idx])["balanced_acc"]
                        - scores(label[idx], pred["threshold"][idx])["balanced_acc"])
    d_lo, d_hi = np.percentile(diff, [2.5, 97.5])
    d = result["model"][0]["balanced_acc"] - result["threshold"][0]["balanced_acc"]
    print(f"\nmodel minus threshold: {d:+.0%} balanced accuracy (95% CI {d_lo:+.0%} to {d_hi:+.0%}, resampling sessions)")
    pd.DataFrame({k: v[0] | {"ci_lo": v[1], "ci_hi": v[2]} for k, v in result.items()}).T.to_csv(
        os.path.splitext(args.out)[0] + ".csv")
    print(f"Saved {os.path.splitext(args.out)[0]}.csv")


if __name__ == "__main__":
    main()
