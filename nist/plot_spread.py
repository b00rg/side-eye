"""Slide figure: how widely BLE signal strength varies at each distance (NIST data).

Usage (from the repo root, after downloading NIST, see nist/README.md):
  python3 nist/plot_spread.py data/external/nist/BLE_RSSI_Data nist/nist_spread.png

Uses every clear-path reading (no one in the way) from NIST LOS rooms 1-5, both device
orientations and both receivers. For each distance it draws the median, the middle 50% and
the middle 80% of readings. The dashed cutoff is the midpoint of the 2 m and 6 m medians,
the same rule `pi/canary.py calibrate` uses, and the title reports how often a single reading
lands on the wrong side of it.
"""
import glob
import os
import re
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

NAME = re.compile(r"CTData_LOS([1-5])_0_O\d_(\d+)cm_Device\d\.csv$")
SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#b5b4ae", "#e8e7e3"
SERIES_1 = "#2a78d6"   # reference palette slot 1, used for the calibration distances
CAL = (200, 600)       # cm: our near / far calibration distances


def load(root):
    parts = []
    for path in glob.glob(os.path.join(root, "LOS", "*", "*.csv")):
        m = NAME.search(os.path.basename(path))
        if m:
            d = pd.read_csv(path, usecols=["RSSI_dBm"])
            d["cm"] = int(m.group(2))
            parts.append(d)
    return pd.concat(parts, ignore_index=True)


def main():
    root, out = sys.argv[1:3]
    df = load(root)
    q = df.groupby("cm").RSSI_dBm.quantile([0.1, 0.25, 0.5, 0.75, 0.9]).unstack()
    near, far = q.loc[CAL[0], 0.5], q.loc[CAL[1], 0.5]
    cutoff = (near + far) / 2
    r2 = df[df.cm == CAL[0]].RSSI_dBm
    r6 = df[df.cm == CAL[1]].RSSI_dBm
    miss_near = (r2 < cutoff).mean()   # 2 m readings that look "far"
    miss_far = (r6 >= cutoff).mean()   # 6 m readings that look "near"

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 11})
    fig, ax = plt.subplots(figsize=(10, 5.6), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    xs = range(len(q))
    for x, (cm, row) in zip(xs, q.iterrows()):
        c = SERIES_1 if cm in CAL else MUTED
        ax.vlines(x, row[0.1], row[0.9], color=c, lw=2, zorder=2)
        ax.vlines(x, row[0.25], row[0.75], color=c, lw=9, zorder=3)
        ax.plot(x, row[0.5], "o", ms=8, mfc=SURFACE, mec=c, mew=2, zorder=4)

    ax.axhline(cutoff, color=INK_2, lw=1.5, ls=(0, (5, 4)), zorder=1)
    for cm in CAL:
        x = list(q.index).index(cm)
        ax.text(x, q.loc[cm, 0.9] + 1.0, f"{cm // 100} m", ha="center", va="bottom",
                color=INK, fontsize=10, fontweight="bold")

    ax.set_xticks(list(xs))
    ax.set_xticklabels([f"{cm / 100:g}" for cm in q.index], color=INK_2)
    ax.set_xlabel("distance between devices (m)", color=INK_2)
    ax.set_ylabel("signal strength, RSSI (dBm)", color=INK_2)
    ax.tick_params(colors=INK_2, length=0)
    ax.grid(axis="y", color=GRID, lw=1)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_visible(False)

    title = fig.suptitle("One reading can't tell 2 m from 6 m", ha="left", fontsize=16,
                         fontweight="bold", color=INK)
    ax.set_title(f"Dashed line = one cutoff ({cutoff:.0f} dBm, midway between the 2 m and 6 m medians).\n"
                 f"{miss_near:.0%} of single readings at 2 m fall below it, and {miss_far:.0%} at 6 m fall above it.\n"
                 "Dot = median, thick bar = middle 50%, thin line = middle 80%. "
                 "NIST BLE RSSI data, clear line of sight, 5 rooms.",
                 loc="left", fontsize=10, color=INK_2, pad=10)
    fig.tight_layout()
    title.set_x(ax.get_position().x0)   # align with the subtitle, which starts at the plot's left edge
    fig.savefig(out, facecolor=SURFACE)

    print(f"{len(df)} readings, {df.cm.nunique()} distances")
    print(f"2 m median {near:.0f} dBm, 6 m median {far:.0f} dBm, cutoff {cutoff:.1f} dBm")
    print(f"2 m readings on the far side: {miss_near:.1%}; 6 m readings on the near side: {miss_far:.1%}")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
