"""Miss vs false-alarm trade-off (DET-style) and TC4TL nDCF, on held-out real walks (exploratory).

Usage (from the repo root):
  python3 nist/det_curve.py --sim nist/sim --real data/runs_session1 --cutoff <threshold_dbm> [--out nist/det_curve.png]

Same walks, folds and models as compare_models.py. At walk level:
  miss rate        = linger walks not alerted on time (missed or early) / linger walks
  false-alarm rate = other walks with any alert / other walks
  nDCF             = miss rate + false-alarm rate (NIST TC4TL pilot plan, weights 1 and 1)
Each model is swept over score cutoffs 0.05-0.95; the rule over waits of 1-8 s. Also reports the
row-level ROC AUC of each model's score. Lower-left on the chart is better.
"""
import argparse
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_models import MODELS, THRESH, load_real, load_sim, score_folds, score_walks  # noqa: E402

SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3"
STYLE = {MODELS[0]: ("#2a78d6", "o", "Model 1 (real only)"),      # reference palette slots 1-3
         MODELS[1]: ("#eb6834", "s", "Model 2 (public data only)"),
         MODELS[2]: ("#1baf7a", "^", "Model 3 (public + real)")}
CUTOFFS = np.round(np.arange(0.05, 0.96, 0.05), 2)
WAITS = [1, 2, 3, 4, 5, 6, 7, 8]


def rates(rows):
    tot = {k: sum(r[k] for r in rows) for k in ("linger_detected", "linger_total", "false_alarm_walks", "negative_walks")}
    miss = 1 - tot["linger_detected"] / max(tot["linger_total"], 1)
    fa = tot["false_alarm_walks"] / max(tot["negative_walks"], 1)
    return miss, fa


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--real", required=True)
    ap.add_argument("--cutoff", type=float, required=True)
    ap.add_argument("--sim-weight", type=float, default=0.2)
    ap.add_argument("--out", default="det_curve.png")
    args = ap.parse_args()

    scored = score_folds(load_sim(args.sim, args.cutoff), load_real(args.real), args.sim_weight)
    rows = []
    for name in MODELS:
        for c in CUTOFFS:
            miss, fa = rates([score_walks(f.assign(_score=f[name]), thresh=c) for f in scored])
            rows.append(dict(method=name, setting=f"cutoff {c:.2f}", value=c, miss=miss, false_alarm=fa))
    for w in WAITS:
        miss, fa = rates([score_walks(f, rule_wait=w) for f in scored])
        rows.append(dict(method="Rule", setting=f"wait {w} s", value=w, miss=miss, false_alarm=fa))
    det = pd.DataFrame(rows)
    det["nDCF"] = det.miss + det.false_alarm
    det.round(3).to_csv("det_curve.csv", index=False)

    allrows = pd.concat(scored)
    print("Row-level ROC AUC (score vs label, held-out walks):")
    for name in MODELS:
        print(f"  {STYLE[name][2]:28s} {roc_auc_score(allrows.label, allrows[name]):.2f}")
    print("\nTC4TL nDCF = miss rate + false-alarm rate (0 = perfect; 1 = same as never alerting; above 1 = worse)")
    pre = {**{m: f"cutoff {THRESH:.2f}" for m in MODELS}, "Rule": "wait 3 s"}
    for m in [*MODELS, "Rule"]:
        d = det[det.method == m]
        at = d[d.setting == pre[m]].iloc[0]
        best = d.loc[d.nDCF.idxmin()]
        print(f"  {m:20s} at pre-registered setting ({pre[m]}): nDCF {at.nDCF:.2f} "
              f"(miss {at.miss:.0%}, false alarm {at.false_alarm:.0%});  best: {best.nDCF:.2f} at {best.setting}")
    print("  (The 'best' setting is chosen on the test walks themselves, so it is optimistic.)")

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 11})
    fig, ax = plt.subplots(figsize=(8.5, 6.5), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for name in MODELS:
        color, marker, label = STYLE[name]
        d = det[det.method == name].sort_values("value")
        ax.plot(d.false_alarm, d.miss, color=color, lw=2, solid_joinstyle="round", solid_capstyle="round", label=label, zorder=2)
        at = d[d.value == THRESH].iloc[0]
        ax.plot(at.false_alarm, at.miss, marker=marker, ms=11, mfc=color, mec=SURFACE, mew=2, zorder=4)
        ax.annotate(label.split(" (")[0], (at.false_alarm, at.miss), xytext=(8, 6), textcoords="offset points",
                    color=INK, fontsize=10, zorder=5)
    r = det[det.method == "Rule"]
    ax.plot(r.false_alarm, r.miss, ls="none", marker="D", ms=8, mfc=INK, mec=SURFACE, mew=2, label="Rule (wait 1-8 s)", zorder=5)
    # Rule labels go below-right; flip one above only if it would hit the previous rule label and no
    # model label (which sits above-right of its marker) is there.
    model_pts = [(d.false_alarm, d.miss) for d in (det[(det.method == m) & (det.value == THRESH)].iloc[0] for m in MODELS)]
    pts = sorted(r.groupby(["false_alarm", "miss"]), key=lambda kv: kv[0])   # several waits can share a point
    prev = None
    for (fa, miss), pt in pts:
        waits = "/".join(f"{int(w)}" for w in sorted(pt.value))
        near_model = any(abs(fa - x) < 0.08 and abs(miss - y) < 0.08 for x, y in model_pts)
        clash = prev is not None and prev[2] == "below" and fa - prev[0] < 0.15 and abs(miss - prev[1]) < 0.05
        side = "above" if clash and not near_model else "below"
        ax.annotate(f"rule {waits} s", (fa, miss), xytext=(7, 7 if side == "above" else -13),
                    textcoords="offset points", color=INK_2, fontsize=9, zorder=6)
        prev = (fa, miss, side)

    ax.set_xlim(-0.03, 1.03)
    ax.set_ylim(-0.03, 1.03)
    ax.set_xlabel("false alarms: share of non-linger walks that buzz", color=INK_2)
    ax.set_ylabel("misses: share of lingers not caught on time", color=INK_2)
    ax.tick_params(colors=INK_2, length=0)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(color=GRID, lw=1)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.legend(loc="center right", bbox_to_anchor=(1.0, 0.45), frameon=False, labelcolor=INK)
    title = fig.suptitle("Misses vs false alarms on held-out walks", ha="left", fontsize=15, fontweight="bold", color=INK)
    ax.set_title("Lines sweep each model's score cutoff (big marker = 0.8, the pre-registered setting);\n"
                 "very low cutoffs buzz before the stop counts, which scores as a miss.\n"
                 "Diamonds: the rule at each wait time. Lower-left is better.",
                 loc="left", fontsize=10, color=INK_2, pad=10)
    fig.tight_layout()
    title.set_x(ax.get_position().x0)
    fig.savefig(args.out, facecolor=SURFACE)
    print(f"\nSaved {args.out} and det_curve.csv")


if __name__ == "__main__":
    main()
