"""System architecture diagram for the slides -> docs/figures/11_architecture.png (no title: the slide has it).

Usage (from the repo root):  python3 scripts/architecture.py
"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "figures",
                   "11_architecture.png")
SURFACE, INK, INK_2, GRID, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3", "#b5b4ae"
BLUE, GREEN = "#2a78d6", "#1baf7a"
BLUE_BG, ZONE_BG = "#e8f1fb", "#f3f2ef"


def zone(ax, x0, y0, x1, y1, label):
    ax.add_patch(FancyBboxPatch((x0, y0), x1 - x0, y1 - y0, boxstyle="round,pad=0,rounding_size=0.25",
                                fc=ZONE_BG, ec=GRID, lw=1.5, zorder=0))
    ax.text(x0 + 0.25, y1 - 0.3, label, fontsize=13, fontweight="bold", color=INK_2, va="top")


def box(ax, x0, y0, w, h, title, sub="", accent=False):
    ax.add_patch(FancyBboxPatch((x0, y0), w, h, boxstyle="round,pad=0,rounding_size=0.12",
                                fc=BLUE_BG if accent else SURFACE, ec=BLUE if accent else MUTED,
                                lw=2 if accent else 1.3, zorder=2))
    cy = y0 + h / 2
    ax.text(x0 + w / 2, cy + (0.17 if sub else 0), title, ha="center", va="center", fontsize=11,
            fontweight="bold", color=INK, zorder=3)
    if sub:
        ax.text(x0 + w / 2, cy - 0.27, sub, ha="center", va="center", fontsize=8.6, color=INK_2,
                zorder=3, linespacing=1.15)
    return {"l": (x0, cy), "r": (x0 + w, cy), "t": (x0 + w / 2, y0 + h), "b": (x0 + w / 2, y0)}


def arrow(ax, a, b, label="", color=INK_2, style="-", conn="arc3", lx=0, ly=0.18, ha="center"):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=14, lw=1.6, color=color,
                                 linestyle=style, connectionstyle=conn, zorder=1, shrinkA=2, shrinkB=2))
    if label:
        ax.text((a[0] + b[0]) / 2 + lx, (a[1] + b[1]) / 2 + ly, label, ha=ha, va="bottom", fontsize=8.6,
                color=color, zorder=4, linespacing=1.15)


def main():
    plt.rcParams.update({"font.family": "sans-serif"})
    fig, ax = plt.subplots(figsize=(16, 8.4), dpi=170)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_xlim(0, 18)
    ax.set_ylim(0.3, 8.8)
    ax.axis("off")

    zone(ax, 2.6, 0.5, 11.55, 8.5, "Raspberry Pi 4 (the wearable)")
    zone(ax, 12.0, 0.5, 17.8, 8.5, "Cloud")

    tx = box(ax, 0.2, 3.85, 2.0, 1.3, "UNO Q", "transmitter\n(stand-in glasses)")

    w, gap, x, y = 1.55, 0.18, 2.85, 3.85
    titles = [("Bluetooth scan", "our packet only"), ("Smooth", "each second"),
              ("Shape inputs", "rise · drop ·\nsteady · held"), ("Shape model", "random forest"),
              ("Alert rule", "score ≥ 0.8\ntwice in 2.5 s")]
    steps = []
    for i, (t, s) in enumerate(titles):
        steps.append(box(ax, x + i * (w + gap), y, w, 1.3, t, s, accent=t == "Shape model"))
    for a, b in zip(steps, steps[1:]):
        arrow(ax, a["r"], b["l"])
    arrow(ax, tx["r"], steps[0]["l"], "adverts\n~9 / s", ly=0.12)

    alert, model = steps[4], steps[3]
    motor = box(ax, alert["b"][0] - w / 2, 1.85, w, 1.15, "Motor buzzes", "GPIO18", accent=False)
    arrow(ax, alert["b"], motor["t"])

    button = box(ax, 5.2, 6.15, 2.7, 1.25, "Wire button", "touch = glasses there\nhold = false alarm")
    upload = box(ax, 8.75, 6.15, 2.55, 1.25, "Upload", "no MAC address,\noffline buffer")
    arrow(ax, alert["t"], upload["b"], "each buzz +\nshape inputs", lx=-0.15, ly=-0.1, ha="right")
    arrow(ax, button["r"], upload["l"], "label")
    ax.text(button["b"][0], button["b"][1] - 0.35, "after a buzz, the wearer labels it", ha="center", va="top",
            fontsize=8.6, color=INK_2, style="italic")

    tiger = box(ax, 12.3, 6.15, 2.55, 1.25, "Tiger Data", "labelled buzzes\n(deleted after 30 days)")
    walks = box(ax, 15.05, 6.15, 2.5, 1.25, "Recorded walks", "sessions 1 + 2\n(ground truth, repo)")
    retrain = box(ax, 12.8, 3.85, 4.3, 1.3, "Retrain (Cloud Run)",
                  "walks + labels; kept only if it does\nbetter on held-out walks", accent=True)
    bucket = box(ax, 12.8, 1.0, 4.3, 1.15, "Model bucket", "shape_model.joblib")
    arrow(ax, upload["r"], tiger["l"])
    arrow(ax, tiger["b"], (tiger["b"][0], retrain["t"][1]))
    arrow(ax, walks["b"], (walks["b"][0], retrain["t"][1]))
    arrow(ax, retrain["b"], bucket["t"])
    # bucket -> Pi: the new model is pulled down and picked up live
    ax.add_patch(FancyArrowPatch(bucket["l"], (model["b"][0], model["b"][1]), arrowstyle="-|>", mutation_scale=14,
                                 lw=1.8, color=BLUE, linestyle=(0, (5, 3)),
                                 connectionstyle=f"angle,angleA=180,angleB=-90,rad=0", zorder=1,
                                 shrinkA=2, shrinkB=2))
    ax.text(model["b"][0] + 0.2, bucket["l"][1] - 0.15, "new model pulled down (fetch_model.sh),\npicked up without a restart",
            fontsize=8.6, color=BLUE, va="top", ha="left")

    fig.tight_layout(pad=0.3)
    fig.savefig(OUT, facecolor=SURFACE)
    print(f"saved {os.path.relpath(OUT)}")


if __name__ == "__main__":
    main()
