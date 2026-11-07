"""Measure obstruction losses from public data, for the bag and blocked walks in simulate_walks.py.

Usage (from the repo root, after downloading both datasets, see nist/README.md):
  python3 nist/obstruction_losses.py data/external/nist/BLE_RSSI_Data data/external/vsc nist/obstruction_losses.json

blocked: NIST BLE RSSI (mds2-2827), LOS scenarios 1-5, Orientation 1 (devices facing each other).
         Loss = median RSSI with a person 0.5 m from a device (sub-scenario 1) minus median RSSI with
         no one in the way (sub-scenario 0), matched on room, receiving device and distance.
         Only distances near our blocked walk are kept (1-3 m).
bag:     MIT-LL BLE RSSI Various Static Configurations, iPhone receiver logs, 6 ft apart.
         Loss = phone held to the ear (TestPhone4, left side) minus phone in a purse (TestPhone2,
         right side), at mirror rotations so both are equally exposed (purse at 270 deg vs ear at
         90 deg, etc.), matched on environment. Approximates "bag at hip height, nothing in the
         way" vs "phone at head height", i.e. our bag-on-a-chair walk vs our ear-height walks.
         The anechoic chamber is excluded: it has no reflections, unlike any real room.

Losses are positive dB to subtract (a negative value means the obstructed signal was stronger).
"""
import glob
import json
import os
import re
import sys

import pandas as pd

NIST_NAME = re.compile(r"CTData_LOS([1-5])_(\d+)_O(\d)_(\d+)cm_Device(\d)\.csv$")
VSC_ANGLE = re.compile(r"_(\d+)\.txt$")
BLOCK_RANGE_CM = (100, 300)
MIRROR = {0: 0, 45: 315, 90: 270, 135: 225, 180: 180, 225: 135, 270: 90, 315: 45}  # ear angle -> purse angle


def nist_block_losses(root):
    rows = []
    for path in glob.glob(os.path.join(root, "LOS", "*", "*.csv")):
        m = NIST_NAME.search(os.path.basename(path))
        if not m:
            continue
        room, sub, orient, cm, dev = map(int, m.groups())
        if orient != 1 or sub not in (0, 1) or not BLOCK_RANGE_CM[0] <= cm <= BLOCK_RANGE_CM[1]:
            continue
        rssi = pd.read_csv(path, usecols=["RSSI_dBm"]).RSSI_dBm
        rows.append(dict(room=room, sub=sub, cm=cm, dev=dev, med=rssi.median()))
    df = pd.DataFrame(rows).pivot_table(index=["room", "cm", "dev"], columns="sub", values="med").dropna()
    return (df[0] - df[1]).round(2).tolist()


def vsc_bag_losses(root):
    rows = []
    for path in glob.glob(os.path.join(root, "*", "iPhone", "*.txt")):
        env = os.path.basename(os.path.dirname(os.path.dirname(path)))
        if "Anechoic" in env:
            continue
        angle = int(VSC_ANGLE.search(path).group(1))
        with open(path, errors="ignore") as f:
            for line in f:
                p = line.rstrip("\n").split(",")
                if len(p) >= 5 and p[1] == "Bluetooth" and p[4] in ("TestPhone2", "TestPhone4"):
                    rssi = int(p[3])
                    if rssi < 0:
                        rows.append((env, angle, p[4], rssi))
    med = pd.DataFrame(rows, columns=["env", "angle", "dev", "rssi"]).groupby(["env", "angle", "dev"]).rssi.median()
    losses = []
    for (env, angle, dev), ear in med.items():
        if dev != "TestPhone4":
            continue
        purse = med.get((env, MIRROR[angle], "TestPhone2"))
        if purse is not None:
            losses.append(round(float(ear - purse), 2))
    return losses


def describe(name, xs):
    s = pd.Series(xs)
    print(f"{name:8s} n={len(s):3d}  median {s.median():+.1f} dB  IQR [{s.quantile(.25):+.1f}, {s.quantile(.75):+.1f}]")


def main():
    nist_root, vsc_root, out = sys.argv[1:4]
    losses = {"blocked": nist_block_losses(nist_root), "bag": vsc_bag_losses(vsc_root)}
    for k, v in losses.items():
        if not v:
            raise SystemExit(f"No {k} losses found; check the dataset path.")
        describe(k, v)
    losses["_source"] = {
        "blocked": "NIST mds2-2827, LOS1-5, O1, person 0.5 m from device vs clear, 1-3 m",
        "bag": "MIT-LL VSC, iPhone rx, ear (TestPhone4) vs purse (TestPhone2) at mirror rotations, anechoic excluded",
    }
    with open(out, "w") as f:
        json.dump(losses, f, indent=1)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
