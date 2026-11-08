"""Retrain the shape model with the labelled alarms collected on Tiger Data.

    python3 pi/retrain_shape.py                     # walks in data/runs_session1 + 2, plus labelled Tiger events
    python3 pi/retrain_shape.py --tiger-weight 0.5  # down-weight Tiger rows
    python3 pi/retrain_shape.py --no-demo           # leave out the phone stand-in
    python3 pi/retrain_shape.py --dry-run           # report only, don't write a model

What Tiger Data adds: every annotated buzz or reported miss from shape_live.py carries the four shape
inputs (rise, drop, steady, held) at that second, labelled by a person: glasses there (TP, FN) -> 1,
false alarm (FP) -> 0. Pending buzzes and rows without shape inputs (from canary.py live) are skipped.

Evaluation stays on held-out whole walks, scored like nist/shape_confirm.py (score >= 0.8 twice within
2.5 s; a linger counts if the first alert comes once the walker has stopped, earlier is "too early";
any alert on another walk is a false alarm; nDCF = miss rate + false-alarm rate). Tiger rows only ever
go into the training side, so both models are judged on exactly the same walks. If adding Tiger rows
makes the held-out score worse, no model is written (use --force to write it anyway).

Output (in --out-dir, default the repo root): shape_model.joblib (what shape_live.py loads),
models/shape_model-<time>.joblib and models/shape_latest.json. On Google Cloud, --out-dir is the
bucket mount (gcp/).
"""

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import joblib  # noqa: E402
import sklearn  # noqa: E402

import train_model  # noqa: E402
from canary_core import ModelAlert  # noqa: E402
from shape_core import SHAPE, ShapeTracker  # noqa: E402

DEFAULT_RUNS = ("data/runs_session1", "data/runs_session2")
EVENTS_SQL = (
    f"SELECT time, glasses_present, {', '.join(SHAPE)} FROM events "
    "WHERE glasses_present IS NOT NULL "
    + "".join(f"AND {name} IS NOT NULL " for name in SHAPE)
    + "{demo_filter}ORDER BY time"
)


def load_walks(folders):
    """{walk id: {"t", "x", "y"}}: each walk's shape inputs, computed in time order as on the Pi."""
    walks = {}
    for folder in folders:
        folder = Path(folder)
        for path in sorted(folder.glob("*.csv")):
            with path.open(newline="") as source:
                rows = sorted(csv.DictReader(source), key=lambda r: float(r["t"]))
            if not rows:
                continue
            tracker = ShapeTracker()
            walks[f"{folder.name}/{path.stem}"] = {
                "t": [float(r["t"]) for r in rows],
                "x": [tracker.update(float(r["mean_rssi"])) for r in rows],
                "y": [int(r["label"]) for r in rows],
            }
    return walks


def fetch_events(url, include_demo=True):
    """Labelled events with shape inputs from Tiger Data, as (x, y)."""
    import psycopg

    sql = EVENTS_SQL.format(demo_filter="" if include_demo else "AND NOT demo ")
    with psycopg.connect(url, connect_timeout=10) as conn:
        records = conn.execute(sql).fetchall()
    return events_to_rows(records)


def events_to_rows(records):
    """(time, glasses_present, rise, drop, steady, held) tuples -> (x, y). Glasses there = 1."""
    return ([[float(v) for v in record[2:]] for record in records],
            [int(bool(record[1])) for record in records])


def fit(walk_items, tiger_x, tiger_y, tiger_weight):
    x = [row for _, w in walk_items for row in w["x"]]
    y = [label for _, w in walk_items for label in w["y"]]
    weights = [1.0] * len(y) + [tiger_weight] * len(tiger_y)
    model = train_model.model_factory().fit(x + tiger_x, y + tiger_y, sample_weight=weights)
    model.feature_names_shape = SHAPE
    return model


def score_walks(walk_items, model):
    """Walk-level result, as nist/shape_confirm.py: hits, too early, false alarms, nDCF."""
    out = {"lingers": 0, "hit": 0, "early": 0, "others": 0, "false_alarm": 0}
    for _, w in walk_items:
        scores = model.predict_proba(w["x"])[:, 1]
        alert, first = ModelAlert(), None
        for t, score in zip(w["t"], scores):
            if alert.update(t, float(score)):
                first = t
                break
        positives = [t for t, label in zip(w["t"], w["y"]) if label]
        if positives:
            out["lingers"] += 1
            if first is not None and first >= positives[0]:
                out["hit"] += 1
            elif first is not None:
                out["early"] += 1
        else:
            out["others"] += 1
            out["false_alarm"] += first is not None
    out["ndcf"] = round((1 - out["hit"] / max(out["lingers"], 1)) + out["false_alarm"] / max(out["others"], 1), 4)
    return out


def cross_validate(walks, tiger_x, tiger_y, tiger_weight):
    """Walks only vs walks + Tiger, on the same held-out walks (train_model's balanced folds)."""
    labels = {wid: [{"label": label} for label in w["y"]] for wid, w in walks.items()}
    totals = {}
    for name, (ex, ey) in (("walks only", ([], [])), ("walks + Tiger", (tiger_x, tiger_y))):
        total = {"lingers": 0, "hit": 0, "early": 0, "others": 0, "false_alarm": 0}
        for held_out in train_model.balanced_group_folds(labels):
            train = [(wid, w) for wid, w in walks.items() if wid not in held_out]
            test = [(wid, walks[wid]) for wid in held_out]
            result = score_walks(test, fit(train, ex, ey, tiger_weight))
            for key in total:
                total[key] += result[key]
        total["ndcf"] = round((1 - total["hit"] / max(total["lingers"], 1))
                              + total["false_alarm"] / max(total["others"], 1), 4)
        totals[name] = total
        print(f"{name:>14}: lingers caught {total['hit']}/{total['lingers']} (too early {total['early']}), "
              f"false alarms {total['false_alarm']}/{total['others']}, nDCF {total['ndcf']:.2f}")
    return totals


def save(model, info, root):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    models = root / "models"
    models.mkdir(parents=True, exist_ok=True)
    versioned = models / f"shape_model-{stamp}.joblib"
    joblib.dump(model, versioned)
    # Write next to the target, then rename: shape_live.py never sees a half-written model.
    tmp = root / "shape_model.joblib.tmp"
    shutil.copyfile(versioned, tmp)
    tmp.replace(root / "shape_model.joblib")
    info = {**info, "model": versioned.name, "trained_at": stamp}
    (models / "shape_latest.json").write_text(json.dumps(info, indent=2) + "\n")
    print(f"Saved {root / 'shape_model.joblib'} (copy: {versioned})")
    return versioned


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", nargs="+", help=f"walk folders (default: {' '.join(DEFAULT_RUNS)})")
    parser.add_argument("--tiger-weight", type=float, default=1.0, help="sample weight of Tiger rows (default 1)")
    parser.add_argument("--no-demo", action="store_true", help="skip events from the phone stand-in")
    parser.add_argument("--min-events", type=int, default=1,
                        help="exit without a new model if fewer labelled Tiger events (default 1)")
    parser.add_argument("--force", action="store_true",
                        help="write the model even if Tiger rows make the held-out walks worse")
    parser.add_argument("--dry-run", action="store_true", help="report only, don't write a model")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="where to write shape_model.joblib and models/ (default: repo root)")
    args = parser.parse_args(argv)

    from alarm_store import service_url

    url = service_url()
    if not url:
        raise SystemExit("No TIMESCALE_SERVICE_URL found (.env, $SIDE_EYE_ENV, or the environment).")
    tiger_x, tiger_y = fetch_events(url, include_demo=not args.no_demo)
    positives = sum(tiger_y)
    print(f"Tiger Data: {len(tiger_y)} labelled events with shape inputs ({positives} glasses there, "
          f"{len(tiger_y) - positives} false alarms); weight {args.tiger_weight}")
    if len(tiger_y) < args.min_events:
        print(f"Fewer than {args.min_events} labelled events: keeping the current model.")
        return 0

    folders = [Path(f) for f in args.runs] if args.runs else [train_model.ROOT / f for f in DEFAULT_RUNS]
    folders = [f for f in folders if f.is_dir()]
    walks = load_walks(folders)
    print(f"Walks: {len(walks)} in {', '.join(str(f) for f in folders) or '(none found)'}")
    if not walks:
        raise SystemExit("No walks found: the shape model needs the recorded walks as its base.")
    info = {"tiger_events": len(tiger_y), "tiger_positive": positives, "tiger_weight": args.tiger_weight,
            "include_demo": not args.no_demo, "walks": len(walks), "sklearn": sklearn.__version__,
            "features": list(SHAPE)}
    info["cv"] = cv = cross_validate(walks, tiger_x, tiger_y, args.tiger_weight)
    if cv["walks + Tiger"]["ndcf"] > cv["walks only"]["ndcf"] and not args.force:
        print("Adding the Tiger rows made the held-out walks worse: keeping the current model "
              "(--force to write it anyway).")
        return 0
    model = fit(list(walks.items()), tiger_x, tiger_y, args.tiger_weight)
    if args.dry_run:
        print("Dry run: no model written.")
        return 0
    save(model, info, args.out_dir or train_model.ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
