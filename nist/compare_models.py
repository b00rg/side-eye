"""Rule vs Model 1 vs Model 2 vs Model 3, all scored on the same held-out REAL walks.

Usage:
  python3 compare_models.py --sim sim_dir --real real_dir --cutoff -68 [--sim-weight 0.2]
  python3 compare_models.py --sim sim_dir --cutoff -68          # sim-only sanity check

Real walk CSVs (from `canary.py record`) must contain, per second:
  mean_rssi, var_rssi, slope, dwell_s, close_streak_s, label
and their filename must contain the walk kind (passer, linger, far, bag, blocked).
Simulated walks are turned into the same per-second rows by `features()` below, which runs
them through pi/canary_core.Tracker: the exact code the Pi uses when recording real walks.

Evaluation uses train_model.py's own split (balanced_group_folds): 4 folds, each holds out
2 linger + 4 other real walks, the same walks train_model.py holds out.
Simulated walks are only ever used for training, never for testing.
"""
import argparse, glob, os, sys
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi"))
from canary_core import MODEL_COLUMNS, Tracker  # noqa: E402  (the Pi's feature code)
from train_model import balanced_group_folds  # noqa: E402  (the Pi's train/test split)

FEATS = list(MODEL_COLUMNS)  # mean_rssi, var_rssi, slope, dwell_s: same columns the Pi's model uses
ROW_FIELDS = [*MODEL_COLUMNS, "close_streak_s", "packet_rate_hz"]
KINDS = ["passer", "linger", "far", "bag", "blocked"]
THRESH = 0.80


def features(readings, labels, cutoff):
    """Per-second rows for one simulated walk, computed by the Pi's own code.

    Feeds each reading into pi/canary_core.Tracker in time order and takes one snapshot at the
    end of every second, exactly as `canary.py record` does once per second. Seconds where the
    Pi would write no row (fewer than 5 readings in the last 5 s, or signal gone for 2 s) are
    skipped, as on the Pi. `cutoff` plays the role of threshold_dbm from settings.json.
    """
    tracker = Tracker()
    t, rssi = readings.t_s.to_numpy(), readings.rssi.to_numpy()
    order = np.argsort(t, kind="stable")
    t, rssi = t[order], rssi[order]
    i, rows = 0, []
    for second, label in zip(labels.second, labels.label):
        now = second + 1.0
        while i < len(t) and t[i] < now:
            tracker.add(float(t[i]), int(rssi[i]))
            i += 1
        snap = tracker.snapshot(now, cutoff)
        if snap is None:
            continue
        rows.append(dict(t=now, **{name: getattr(snap, name) for name in ROW_FIELDS}, label=int(label)))
    return pd.DataFrame(rows, columns=["t", *ROW_FIELDS, "label"])


def load_sim(sim_dir, cutoff):
    walks = []
    for lab_path in sorted(glob.glob(os.path.join(sim_dir, "*_labels.csv"))):
        name = os.path.basename(lab_path).replace("_labels.csv", "")
        rd = pd.read_csv(lab_path.replace("_labels.csv", "_readings.csv"))
        f = features(rd, pd.read_csv(lab_path), cutoff)
        f["walk"] = name; f["kind"] = name.split("_")[-1]
        walks.append(f)
    return pd.concat(walks, ignore_index=True)


def load_real(real_dir):
    walks = []
    for p in sorted(glob.glob(os.path.join(real_dir, "*.csv"))):
        f = pd.read_csv(p)
        name = os.path.basename(p)
        f["walk"] = name
        f["kind"] = next((k for k in KINDS if k in name), "other")
        walks.append(f)
    return pd.concat(walks, ignore_index=True)


def folds(test, k=4):
    """Held-out walk sets, using pi/train_model.py's own split so both scripts test the same walks.

    Walks are passed in file order (load_real reads them sorted, like train_model.load_runs),
    so balanced_group_folds' fixed-seed shuffle gives the identical folds.
    """
    runs = {w: [{"label": int(x)} for x in g.label] for w, g in test.groupby("walk", sort=False)}
    yield from (set(f) for f in balanced_group_folds(runs, n_splits=k))


def walk_alerts(scores, t_s, thresh=THRESH, gap=2.5):
    """Model alert = two rows >= thresh no more than `gap` seconds apart. Returns first alert second or None."""
    hits = t_s[scores >= thresh]
    for a, b in zip(hits[:-1], hits[1:]):
        if b - a <= gap:
            return b
    return None


def score_walks(test, scores, rule=False):
    out = dict(linger_detected=0, linger_total=0, false_alarm_walks=0, negative_walks=0, early_alerts=0,
               tp=0, fp=0, fn=0)
    for w, g in test.groupby("walk"):
        g = g.reset_index(drop=True)
        t = g.index.values.astype(float)
        s = scores[g.index] if False else g["_score"].values
        if rule:
            hit = np.where(g.close_streak_s.values >= 3)[0]
            first = t[hit[0]] if len(hit) else None
            pred = (g.close_streak_s.values >= 3).astype(int)
        else:
            first = walk_alerts(s, t)
            pred = (s >= THRESH).astype(int)
        y = g.label.values
        out["tp"] += int(((pred == 1) & (y == 1)).sum())
        out["fp"] += int(((pred == 1) & (y == 0)).sum())
        out["fn"] += int(((pred == 0) & (y == 1)).sum())
        if g.kind.iloc[0] == "linger":
            out["linger_total"] += 1
            if first is not None:
                pos = np.where(y == 1)[0]
                if len(pos) and first < pos[0]:
                    out["early_alerts"] += 1
                else:
                    out["linger_detected"] += 1
        else:
            out["negative_walks"] += 1
            if first is not None:
                out["false_alarm_walks"] += 1
    return out


def fit(train, weights=None, seed=0):
    m = RandomForestClassifier(n_estimators=20, max_depth=4, random_state=seed, class_weight="balanced")
    m.fit(train[FEATS].values, train.label.values, sample_weight=weights)
    return m


def summarise(name, rs):
    tot = {k: sum(r[k] for r in rs) for k in rs[0]}
    prec = tot["tp"] / max(tot["tp"] + tot["fp"], 1)
    rec = tot["tp"] / max(tot["tp"] + tot["fn"], 1)
    return dict(method=name,
                linger_detected=f'{tot["linger_detected"]}/{tot["linger_total"]}',
                false_alarm_walks=f'{tot["false_alarm_walks"]}/{tot["negative_walks"]}',
                early_alerts=tot["early_alerts"], precision=round(prec, 2), recall=round(rec, 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--real")
    ap.add_argument("--cutoff", type=float, required=True)
    ap.add_argument("--sim-weight", type=float, default=0.2,
                    help="weight of each simulated row relative to a real row in Model 3")
    args = ap.parse_args()

    sim = load_sim(args.sim, args.cutoff)
    if args.real:
        test_pool = load_real(args.real)
    else:  # sanity mode: hold out 24 sim walks as stand-in "real" walks
        lin = sim[sim.kind == "linger"].walk.unique()[:8]
        oth = sim[sim.kind != "linger"].walk.unique()[:16]
        held = set(lin) | set(oth)
        test_pool = sim[sim.walk.isin(held)].copy()
        sim = sim[~sim.walk.isin(held)]
        print("SANITY MODE: no real walks given, testing on 24 held-out simulated walks\n")

    results = {k: [] for k in ["Rule (3 s)", "Model 1: real only", "Model 2: sim only", "Model 3: sim + real"]}
    m2 = fit(sim)
    for held in folds(test_pool):
        test = test_pool[test_pool.walk.isin(held)].copy()
        train_real = test_pool[~test_pool.walk.isin(held)]

        test["_score"] = 0.0
        results["Rule (3 s)"].append(score_walks(test, None, rule=True))

        m1 = fit(train_real)
        test["_score"] = m1.predict_proba(test[FEATS].values)[:, 1]
        results["Model 1: real only"].append(score_walks(test, None))

        test["_score"] = m2.predict_proba(test[FEATS].values)[:, 1]
        results["Model 2: sim only"].append(score_walks(test, None))

        both = pd.concat([sim, train_real])
        w = np.concatenate([np.full(len(sim), args.sim_weight), np.ones(len(train_real))])
        m3 = fit(both, w)
        test["_score"] = m3.predict_proba(test[FEATS].values)[:, 1]
        results["Model 3: sim + real"].append(score_walks(test, None))

    table = pd.DataFrame([summarise(k, v) for k, v in results.items()])
    print(table.to_string(index=False))
    table.to_csv("comparison_results.csv", index=False)
    print("\nSaved comparison_results.csv")


if __name__ == "__main__":
    main()
