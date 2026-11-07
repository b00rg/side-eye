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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pi"))
from canary_core import MODEL_COLUMNS, Tracker  # noqa: E402  (the Pi's feature code)
from train_model import balanced_group_folds, model_factory  # noqa: E402  (the Pi's split and model)

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
        if f.empty:   # the Pi would have written no rows (too few readings), so skip the walk
            continue
        f["walk"] = name; f["kind"] = name.split("_")[-1]
        walks.append(f)
    total = len(glob.glob(os.path.join(sim_dir, "*_labels.csv")))
    if len(walks) < total:
        print(f"Note: {total - len(walks)} of {total} simulated walks produced no rows "
              "(too few readings for the Pi's 5-in-5-s check) and were skipped.")
    out = pd.concat(walks, ignore_index=True)
    out["label"] = out.label.astype(int)
    return out


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


def score_walks(test, thresh=THRESH, rule_wait=None):
    """Score every walk in `test`. rule_wait=None scores the model column `_score` at `thresh`;
    otherwise scores the rule: alert once close_streak_s reaches rule_wait seconds."""
    out = dict(linger_detected=0, linger_total=0, false_alarm_walks=0, negative_walks=0, early_alerts=0,
               tp=0, fp=0, fn=0, delays=[])
    for _, g in test.groupby("walk", sort=False):
        t = (g["t"] - g["t"].iloc[0]).to_numpy(float) if "t" in g else np.arange(len(g), dtype=float)
        y = g.label.to_numpy()
        if rule_wait is not None:
            pred = (g.close_streak_s.to_numpy() >= rule_wait).astype(int)
            first = t[pred.argmax()] if pred.any() else None
        else:
            s = g["_score"].to_numpy()
            pred = (s >= thresh).astype(int)
            first = walk_alerts(s, t, thresh)
        out["tp"] += int(((pred == 1) & (y == 1)).sum())
        out["fp"] += int(((pred == 1) & (y == 0)).sum())
        out["fn"] += int(((pred == 0) & (y == 1)).sum())
        if g.kind.iloc[0] == "linger":
            out["linger_total"] += 1
            pos = np.flatnonzero(y == 1)
            if first is not None and len(pos):
                if first < t[pos[0]]:
                    out["early_alerts"] += 1
                else:
                    out["linger_detected"] += 1
                    out["delays"].append(first - (t[pos[0]] - 3))  # labels start 3 s after the stop
        else:
            out["negative_walks"] += 1
            if first is not None:
                out["false_alarm_walks"] += 1
    return out


def fit(train, weights=None):
    """Same forest as the Pi's train_model.py (20 trees, depth 4, balanced, seed 42)."""
    m = model_factory()
    m.fit(train[FEATS].values, train.label.values, sample_weight=weights)
    return m


def summarise(name, rs, **extra):
    tot = {k: sum(r[k] for r in rs) for k in rs[0] if k != "delays"}
    delays = [d for r in rs for d in r["delays"]]
    prec = tot["tp"] / max(tot["tp"] + tot["fp"], 1)
    rec = tot["tp"] / max(tot["tp"] + tot["fn"], 1)
    return dict(method=name, **extra,
                linger_detected=f'{tot["linger_detected"]}/{tot["linger_total"]}',
                false_alarm_walks=f'{tot["false_alarm_walks"]}/{tot["negative_walks"]}',
                early_alerts=tot["early_alerts"],
                stop_to_alert_s=round(float(np.median(delays)), 1) if delays else None,
                precision=round(prec, 2), recall=round(rec, 2))


def floats(text):
    return [float(x) for x in text.split(",") if x.strip()]


MODELS = ["Model 1: real only", "Model 2: sim only", "Model 3: sim + real"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--real")
    ap.add_argument("--cutoff", type=float, required=True)
    ap.add_argument("--sim-weight", type=float, default=0.2,
                    help="weight of each simulated row relative to a real row in Model 3")
    ap.add_argument("--thresholds", type=floats, default=[0.6, 0.8, 0.9],
                    help="model score cutoffs to sweep, comma-separated (main table uses 0.8)")
    ap.add_argument("--rule-waits", type=floats, default=[1, 2, 3, 4, 5, 6],
                    help="seconds the rule waits before alerting, comma-separated (main table uses 3)")
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

    # Train once per fold; keep each held-out fold with all three models' scores.
    m2 = fit(sim)
    scored = []
    for held in folds(test_pool):
        test = test_pool[test_pool.walk.isin(held)].copy()
        train_real = test_pool[~test_pool.walk.isin(held)]
        both = pd.concat([sim, train_real])
        w = np.concatenate([np.full(len(sim), args.sim_weight), np.ones(len(train_real))])
        for name, m in zip(MODELS, (fit(train_real), m2, fit(both, w))):
            test[name] = m.predict_proba(test[FEATS].values)[:, 1]
        scored.append(test)

    def model_rows(name, thresh):
        return [score_walks(f.assign(_score=f[name]), thresh=thresh) for f in scored]

    table = pd.DataFrame([summarise("Rule (3 s)", [score_walks(f, rule_wait=3) for f in scored])]
                         + [summarise(n, model_rows(n, THRESH)) for n in MODELS])
    rule_sweep = pd.DataFrame([summarise("Rule", [score_walks(f, rule_wait=wt) for f in scored], wait_s=wt)
                               for wt in args.rule_waits])
    thresh_sweep = pd.DataFrame([summarise(n, model_rows(n, th), cutoff=th)
                                 for n in MODELS for th in args.thresholds])

    print(table.to_string(index=False))
    print("\nRule: how long to wait before alerting")
    print(rule_sweep.drop(columns="method").to_string(index=False))
    print("\nModels: score cutoff for an alert")
    print(thresh_sweep.to_string(index=False))
    table.to_csv("comparison_results.csv", index=False)
    rule_sweep.to_csv("rule_wait_sweep.csv", index=False)
    thresh_sweep.to_csv("threshold_sweep.csv", index=False)
    print("\nSaved comparison_results.csv, rule_wait_sweep.csv, threshold_sweep.csv")
    print("stop_to_alert_s = median seconds from the stop to the alert, for lingers caught in time.")


if __name__ == "__main__":
    main()
