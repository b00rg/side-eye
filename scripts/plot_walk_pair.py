"""Slide figure: a walk-past and a linger side by side (signal, Model 3 score, alerts).

Usage (from the repo root, after model.joblib exists):
  python3 scripts/plot_walk_pair.py --out results/walk_pair.png                      # first passer + linger in data/runs
  python3 scripts/plot_walk_pair.py --passer <file> --linger <file> --out ...        # pick the walks
  python3 scripts/plot_walk_pair.py --db "$DATABASE_URL" --out ...                   # read the walks from Tiger

Each column: signal against the near/far cutoff, then Model 3's score against 0.8. Shaded band =
when the person was stopped (linger only, from the recording's labels). Solid line = Model 3's
alert (BUZZ); dotted line = where the old 3-second rule would have buzzed.
"""
import argparse
import glob
import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import joblib
import matplotlib.pyplot as plt
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pi"))
from canary_core import MODEL_COLUMNS, ModelAlert  # noqa: E402

SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3"
SIGNAL, SCORE = "#2a78d6", "#eb6834"


def from_files(passer, linger):
    pick = lambda kind, given: given or sorted(glob.glob(os.path.join(ROOT, "data", "runs", f"*-{kind}.csv")))[0]
    return pd.read_csv(pick("passer", passer)), pd.read_csv(pick("linger", linger))


def from_tiger(url):
    import psycopg
    cols = "t, mean_rssi, var_rssi, slope, dwell_s, close_streak_s, label"
    with psycopg.connect(url) as conn:
        out = []
        for kind in ("passer", "linger"):
            run_id = conn.execute("SELECT run_id FROM walks WHERE scenario = %s ORDER BY run_id LIMIT 1", (kind,)).fetchone()[0]
            rows = conn.execute(f"SELECT {cols} FROM walk_rows WHERE run_id = %s ORDER BY time", (run_id,)).fetchall()
            out.append(pd.DataFrame(rows, columns=cols.split(", ")).assign(run_id=run_id))
    return out


def events(d, model):
    scores = model.predict_proba(d[list(MODEL_COLUMNS)].values)[:, 1]
    t = (d.t - d.t.iloc[0]).to_numpy()
    alert, buzz, rule, last_m, last_r = ModelAlert(), [], [], -1e9, -1e9
    for ti, s, streak in zip(t, scores, d.close_streak_s):
        if alert.update(ti, s) and ti - last_m >= 15:
            buzz.append(ti)
            last_m = ti
        if streak >= 3 and ti - last_r >= 15:
            rule.append(ti)
            last_r = ti
    return t, scores, buzz, rule


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--passer")
    ap.add_argument("--linger")
    ap.add_argument("--db", help="read the walks from Tiger instead of data/runs")
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "walk_pair.png"))
    args = ap.parse_args()
    walks = from_tiger(args.db) if args.db else from_files(args.passer, args.linger)
    model = joblib.load(os.path.join(ROOT, "model.joblib"))
    cutoff = json.load(open(os.path.join(ROOT, "settings.json")))["threshold_dbm"]

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 11})
    fig, axes = plt.subplots(2, 2, figsize=(13, 6.8), sharey="row", gridspec_kw={"height_ratios": [3, 2]}, dpi=170)
    fig.patch.set_facecolor(SURFACE)
    for col, (d, name) in enumerate(zip(walks, ("Walking past", "Lingering"))):
        t, scores, buzz, rule = events(d, model)
        top, bottom = axes[0, col], axes[1, col]
        for ax in (top, bottom):
            ax.set_facecolor(SURFACE)
            ax.grid(axis="y", color=GRID, lw=1)
            ax.set_axisbelow(True)
            for side in ax.spines.values():
                side.set_visible(False)
            ax.tick_params(colors=INK_2, length=0)
        pos = t[d.label.to_numpy() == 1]
        if len(pos):   # stopped: from 3 s before the first positive second to the last one
            for ax in (top, bottom):
                ax.axvspan(pos[0] - 3, pos[-1], color=INK_2, alpha=0.08, lw=0)
            top.text((pos[0] - 3 + pos[-1]) / 2, -88, "stopped", color=INK_2, ha="center", fontsize=10)
        top.axhline(cutoff, color=INK_2, lw=1.5, ls=(0, (5, 4)))
        top.plot(t, d.mean_rssi, color=SIGNAL, lw=2.5)
        bottom.axhline(0.8, color=INK_2, lw=1.5, ls=(0, (5, 4)))
        bottom.plot(t, scores, color=SCORE, lw=2.5)
        for b in buzz:
            for ax in (top, bottom):
                ax.axvline(b, color=INK, lw=2.5)
            top.text(b + 0.4, -27, "BUZZ", color=INK, fontweight="bold", va="top")
        for r in rule:
            for ax in (top, bottom):
                ax.axvline(r, color=INK_2, lw=1.2, ls=":")
            top.text(r + 0.4, -94, "3-s rule\nwould buzz", color=INK_2, fontsize=9, va="bottom")
        verdict = ("Model 3 stays quiet" if not buzz else "Model 3 buzzes") + \
                  ("; the old rule would buzz" if rule else "; the old rule stays quiet")
        top.set_title(f"{name}: {verdict}", loc="left", fontsize=12, color=INK, pad=8)
        top.set_ylim(-95, -25)
        bottom.set_ylim(-0.03, 1.05)
        bottom.set_xlabel("seconds", color=INK_2)
        if col == 0:
            top.set_ylabel("signal (dBm)", color=INK_2)
            bottom.set_ylabel("linger score", color=INK_2)
            top.text(t[0], cutoff + 1, f"cutoff {cutoff:.0f} dBm", color=INK_2, fontsize=9, ha="left", va="bottom")
            bottom.text(t[0], 0.82, "alert at 0.8", color=INK_2, fontsize=9, ha="left", va="bottom")
    title = fig.suptitle("Same signal strength, different behaviour: Side Eye tells walking past from lingering",
                         ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.tight_layout()
    title.set_x(axes[0, 0].get_position().x0)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fig.savefig(args.out, facecolor=SURFACE)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
