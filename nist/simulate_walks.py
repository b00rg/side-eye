"""Generate synthetic Canary walks from MITRE Range-Angle (Structured) Bluetooth data.

Usage:
  python3 simulate_walks.py mitre_rssi.csv settings.json out_dir [--n 300] [--rate 8] [--seed 0]

  mitre_rssi.csv  output of parse_mitre.py
  settings.json   the Pi's calibration file (needs your 2 m and 6 m medians, see NEAR_KEYS/FAR_KEYS)
  --rate          readings per second the Pi actually sees (check packet_rate_hz in your real CSVs)

How it works
  1. Keep only walks where the transmitter was an Android phone running nRF Connect,
     because that matches our fake glasses. Drop the 127 dBm invalid readings.
  2. Fit a log-distance path-loss line, rssi = A - 10 n log10(d), so we know the typical
     signal at any distance, including 6 m (MITRE stops at 4.57 m).
  3. Calibrate to our hardware: a straight-line map that sends MITRE's predicted 2 m and 6 m
     values onto the 2 m and 6 m medians our Pi measured in Section 5.
  4. Noise: for each walk we pick one real MITRE recording and replay its deviations from the
     median in time order. That keeps real fluctuation, real autocorrelation, and per-session
     body effects, instead of independent random noise every second.
  5. Each walk follows a second-by-second distance path matching our real walk scripts, and
     every second we emit `rate` readings. Output per walk is raw readings (t_s, rssi) plus
     a labels file, in the same layout the Pi's feature code can consume.
"""
import argparse, json, os
import numpy as np
import pandas as pd

NEAR_KEYS = ["near_median", "near_median_dbm", "median_2m"]
FAR_KEYS = ["far_median", "far_median_dbm", "median_6m"]
BLOCK_DB = 6.0   # fallback only: used if nist/obstruction_losses.json is missing
BAG_DB = 9.0     # fallback only
LOSSES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "obstruction_losses.json")


def load_losses(path):
    """Measured losses (dB) per walk kind, from nist/obstruction_losses.py; constants if missing."""
    if path and os.path.exists(path):
        with open(path) as f:
            d = json.load(f)
        return {"bag": d["bag"], "blocked": d["blocked"]}, f"measured ({path})"
    return {"bag": [BAG_DB], "blocked": [BLOCK_DB]}, "fallback constants"


def pick(d, keys):
    for k in keys:
        if k in d:
            return float(d[k])
    raise KeyError(f"settings.json needs one of {keys}; found {list(d)}")


def fit_path_loss(df):
    med = df.groupby("range_m").rssi.median()
    x = np.log10(med.index.values)
    slope, A = np.polyfit(x, med.values, 1)
    return A, -slope / 10.0, med  # A, path-loss exponent n, medians


def rssi_mean(d, A, n):
    return A - 10 * n * np.log10(np.maximum(d, 0.3))


# ---------- walk paths: distance (m) for each second, plus per-second label ----------

def path_passer(rng):
    start, closest, speed = 6.0, rng.uniform(1.2, 2.0), rng.uniform(1.0, 1.6)
    t = np.arange(0, 2 * start / speed + 1)
    x = -start + speed * t               # walk along a line past the Pi
    d = np.sqrt(x ** 2 + closest ** 2)
    return d, np.zeros(len(d), int)


def path_linger(rng):
    speed, stop_d, stay = rng.uniform(0.9, 1.5), rng.uniform(1.5, 2.5), int(rng.uniform(12, 25))
    approach = np.linspace(6.0, stop_d, int((6.0 - stop_d) / speed) + 1)
    leave = approach[::-1]
    d = np.concatenate([approach, np.full(stay, stop_d), leave])
    y = np.zeros(len(d), int)
    s0 = len(approach)
    y[s0 + 3: s0 + stay] = 1             # same rule as the Pi: positive after the first 3 s of the stop
    return d, y


def path_far(rng):
    d = np.full(int(rng.uniform(20, 30)), rng.uniform(3.5, 6.0))
    return d, np.zeros(len(d), int)


def path_static(rng, lo, hi):
    d = np.full(int(rng.uniform(25, 35)), rng.uniform(lo, hi))
    return d, np.zeros(len(d), int)


MIX = {"passer": 0.35, "linger": 0.35, "far": 0.1, "bag": 0.1, "blocked": 0.1}


def simulate(df, A, n, a, b, kind, rng, rate, losses):
    if kind == "passer":
        d, y = path_passer(rng); extra = 0.0
    elif kind == "linger":
        d, y = path_linger(rng); extra = 0.0
    elif kind == "far":
        d, y = path_far(rng); extra = 0.0
    else:  # bag or blocked: one measured loss, drawn per walk
        d, y = path_static(rng, 1.5, 2.5); extra = float(rng.choice(losses[kind]))

    # one real recording supplies the noise for this whole walk
    f = rng.choice(df.file.unique())
    rec = df[df.file == f].sort_values("t_s")
    resid = (rec.rssi - rec.groupby("range_m").rssi.transform("median")).values
    resid = np.clip(resid, -15, 15)   # a few MITRE readings jump 30+ dB; treat as outliers
    need = len(d) * rate
    reps = int(np.ceil(need / len(resid))) + 1
    noise = np.tile(resid, reps)
    off = rng.integers(0, len(resid))
    noise = noise[off: off + need]

    t = np.repeat(np.arange(len(d)), rate) + np.tile(np.arange(rate) / rate, len(d))
    # calibrate only the distance curve; obstruction loss and real noise stay in dB
    base = a * rssi_mean(np.repeat(d, rate), A, n) + b - extra
    rssi = np.round(base + noise).astype(int)
    readings = pd.DataFrame({"t_s": t, "rssi": rssi, "true_d_m": np.repeat(d, rate)})
    labels = pd.DataFrame({"second": np.arange(len(d)), "label": y, "true_d_m": d})
    return readings, labels, f, extra


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mitre_csv"); p.add_argument("settings"); p.add_argument("out_dir")
    p.add_argument("--n", type=int, default=300)
    p.add_argument("--rate", type=int, default=8)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--losses", default=LOSSES, help="obstruction losses JSON from nist/obstruction_losses.py")
    args = p.parse_args()

    df = pd.read_csv(args.mitre_csv)
    df = df[df.tx_android & (df.rssi < 0)]
    A, n, med = fit_path_loss(df)
    s = json.load(open(args.settings))
    near, far = pick(s, NEAR_KEYS), pick(s, FAR_KEYS)
    m2, m6 = rssi_mean(2.0, A, n), rssi_mean(6.0, A, n)
    a = (near - far) / (m2 - m6)
    b = near - a * m2

    os.makedirs(args.out_dir, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    losses, loss_src = load_losses(args.losses)
    kinds = rng.choice(list(MIX), size=args.n, p=list(MIX.values()))
    manifest = []
    for i, k in enumerate(kinds):
        r, lab, src, extra = simulate(df, A, n, a, b, k, rng, args.rate, losses)
        name = f"sim_{i:04d}_{k}"
        r.to_csv(os.path.join(args.out_dir, name + "_readings.csv"), index=False)
        lab.to_csv(os.path.join(args.out_dir, name + "_labels.csv"), index=False)
        manifest.append(dict(walk=name, kind=k, seconds=len(lab), positives=int(lab.label.sum()), mitre_file=src, loss_db=extra))
    pd.DataFrame(manifest).to_csv(os.path.join(args.out_dir, "manifest.csv"), index=False)

    info = dict(obstruction_losses=loss_src, A=A, n=n, mitre_pred_2m=m2, mitre_pred_6m=m6, scale=a, shift=b,
                pi_near=near, pi_far=far, android_files=int(df.file.nunique()),
                mitre_medians={str(k): float(v) for k, v in med.items()})
    json.dump(info, open(os.path.join(args.out_dir, "calibration.json"), "w"), indent=2)
    print(f"obstruction losses: {loss_src}")
    print(f"path loss: A={A:.1f} dBm at 1 m, n={n:.2f}")
    print(f"MITRE 2 m {m2:.1f} -> Pi {near:.1f};  MITRE 6 m {m6:.1f} -> Pi {far:.1f}  (scale {a:.2f}, shift {b:.1f})")
    print(pd.DataFrame(manifest).kind.value_counts().to_string())


if __name__ == "__main__":
    main()
