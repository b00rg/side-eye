"""Parse MITRE Range-Angle (Structured) logs into one tidy CSV of RSSI readings.

Output columns: set, file, tx_device, tx_android, environment, rx_carry, tx_carry,
range_ft, range_m, angle_deg, t_s (seconds from first reading in file), rssi
"""
import sys, glob, os
import pandas as pd

root = sys.argv[1]
out = sys.argv[2]
idx = pd.read_excel(os.path.join(root, "Index.xlsx")).set_index("set")

rows = []
for path in sorted(glob.glob(os.path.join(root, "set_*", "*.txt"))):
    s = os.path.basename(os.path.dirname(path))
    if s not in idx.index:
        continue
    meta = idx.loc[s]
    rng = ang = None
    readings = []
    with open(path, errors="ignore") as f:
        for line in f:
            p = line.rstrip("\n").split(",")
            if len(p) < 3:
                continue
            if p[1] == "Range":
                rng = float(p[2])
            elif p[1] == "Angle":
                ang = float(p[2])
            elif p[1] == "Bluetooth" and len(p) >= 5 and p[4].strip() == "BlueProxTx":
                try:
                    readings.append((pd.Timestamp(p[0]), int(p[3])))
                except ValueError:
                    pass
    if rng is None or not readings:
        continue
    t0 = readings[0][0]
    tx = str(meta["partner_tester"])
    for ts, r in readings:
        rows.append(dict(
            set=s, file=os.path.basename(path), tx_device=tx,
            tx_android=not tx.lower().startswith("iphone"),
            environment=f'{meta["environment"]} / {meta["environment_1"]}',
            rx_carry=meta["self_on_body_location"], tx_carry=meta["partner_on_body_location"],
            range_ft=rng, range_m=round(rng * 0.3048, 2), angle_deg=ang,
            t_s=(ts - t0).total_seconds(), rssi=r))

df = pd.DataFrame(rows)
df.to_csv(out, index=False)
print(f"{len(df)} readings, {df.file.nunique()} files, {df.set.nunique()} sets")
