"""Slide figure: the models work when the signal carries distance; our real room doesn't give it.

Usage (from the repo root):
  python3 scripts/concept_figure.py --out results/concept.png

Same methods (3-second rule, Models 1-3, never alert), same folds and the same score in two worlds:
  left:  simulated walks where signal falls off with distance (the public-data simulator's 'working'
         case: 2 m = -45, 6 m = -65 dBm, 8 readings/s). Training AND test walks are simulated, so this
         shows the concept, not real-world performance.
  right: our 24 real session-1 walks (data/runs_session1), evaluated as pre-registered.
Score: TC4TL nDCF = miss rate + false-alarm rate (walk level). Lower is better; 1.0 = no better than
never buzzing.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nist"))
from compare_models import MODELS, load_real, load_sim, score_folds, score_walks  # noqa: E402

MITRE = os.path.join(ROOT, "data", "external", "mitre_rssi.csv")
SURFACE, INK, INK_2, GRID, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3", "#b5b4ae"
BAR = "#2a78d6"
LABELS = ["3-second\nrule", "Model 1\n(real)", "Model 2\n(public)", "Model 3\n(both)", "Never\nbuzz"]


def ndcf(rows):
    hit = sum(r["linger_detected"] for r in rows)
    lingers = sum(r["linger_total"] for r in rows)
    fa = sum(r["false_alarm_walks"] for r in rows)
    negatives = sum(r["negative_walks"] for r in rows)
    return (1 - hit / lingers) + fa / negatives


def evaluate(walks_dir, settings, tmp, rate):
    cutoff = json.load(open(settings))["threshold_dbm"]
    sim = os.path.join(tmp, "sim_" + os.path.basename(walks_dir.rstrip("/")))
    subprocess.run([sys.executable, "-W", "ignore", os.path.join(ROOT, "nist", "simulate_walks.py"), MITRE, settings,
                    sim, "--n", "300", "--rate", str(rate), "--seed", "0"], check=True, stdout=subprocess.DEVNULL)
    scored = score_folds(load_sim(sim, cutoff), load_real(walks_dir))
    scores = [ndcf([score_walks(f, rule_wait=3) for f in scored])]
    scores += [ndcf([score_walks(f.assign(_score=f[m])) for f in scored]) for m in MODELS]
    scores.append(ndcf([score_walks(f.assign(_score=0.0), thresh=2.0) for f in scored]))
    return scores


def panel(ax, scores, title, subtitle, line_label=False):
    ax.set_facecolor(SURFACE)
    colors = [BAR] * 4 + [MUTED]
    ax.bar(range(5), scores, width=0.6, color=colors, zorder=3)
    ax.axhline(1.0, color=INK_2, lw=1.5, ls=(0, (5, 4)), zorder=4)
    if line_label:   # once, in the gap above the short model bars (both panels share the line)
        ax.text(2.0, 1.04, "no better than never buzzing", color=INK_2, fontsize=9, ha="center", va="bottom")
    for x, v in enumerate(scores):
        ax.text(x, v + 0.04, f"{v:.2f}", ha="center", va="bottom", color=INK, fontsize=10, fontweight="bold")
    ax.set_xticks(range(5))
    ax.set_xticklabels(LABELS, color=INK_2, fontsize=9)
    ax.set_ylim(0, 2.1)
    ax.grid(axis="y", color=GRID, lw=1)
    ax.set_axisbelow(True)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.tick_params(colors=INK_2, length=0)
    ax.set_title(f"{title}\n", loc="left", fontsize=12, fontweight="bold", color=INK)
    ax.text(0, 1.02, subtitle, transform=ax.transAxes, fontsize=9, color=INK_2, va="bottom")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "concept.png"))
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        world = os.path.join(tmp, "working")
        subprocess.run([sys.executable, "-W", "ignore", os.path.join(ROOT, "scripts", "make_placeholder_runs.py"), MITRE,
                        "--out", world], check=True, stdout=subprocess.DEVNULL)
        sim_scores = evaluate(os.path.join(world, "runs"), os.path.join(world, "settings.json"), tmp, 8)
        real_dir = os.path.join(ROOT, "data", "runs_session1")
        real_scores = evaluate(real_dir, os.path.join(real_dir, "settings.json"), tmp, 11.4)

    for name, sc in (("simulated", sim_scores), ("real room", real_scores)):
        print(f"{name:10s} " + "  ".join(f"{l.replace(chr(10), ' ')} {v:.2f}" for l, v in zip(LABELS, sc)))
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10})
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.2), sharey=True, dpi=170)
    fig.patch.set_facecolor(SURFACE)
    panel(axes[0], sim_scores, "When the signal changes with distance",
          "simulated walks from public data (training and test simulated)", line_label=True)
    panel(axes[1], real_scores, "Our real room (session 1, 24 walks)",
          "signal barely changes between 2 m and 6 m")
    axes[0].set_ylabel("misses + false alarms (TC4TL nDCF)\nlower is better", color=INK_2)
    title = fig.suptitle("The models work when the signal carries distance. Our room doesn't provide it.",
                         ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.tight_layout()
    title.set_x(axes[0].get_position().x0)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    fig.savefig(args.out, facecolor=SURFACE)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
