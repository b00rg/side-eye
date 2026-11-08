"""Retrain the linger model with the labelled alarms collected on Tiger Data.

    python3 pi/retrain.py                    # walks in data/runs_session1 + 2 (or data/runs/) + labelled Tiger events
    python3 pi/retrain.py --tiger-weight 0.5 # down-weight Tiger rows (like --sim-weight)
    python3 pi/retrain.py --no-demo          # leave out the nRF Connect phone stand-in
    python3 pi/retrain.py --dry-run          # report only, don't write a model

What Tiger Data adds: every annotated case is one row of the same four features the model uses,
labelled by a person: TP and FN -> 1 (glasses there), FP -> 0. Pending buzzes are skipped.
True negatives are never stored, so Tiger rows are all "hard" cases near the rule's boundary;
the walks in data/runs/ still provide the ordinary negatives.

Evaluation stays on held-out whole walks (train_model's folds). Tiger rows are only ever added
to the training side, so the two models are compared on exactly the same test walks.
Output (in --out-dir, default the repo root): model.joblib (what canary.py loads),
models/model-<time>.joblib and models/latest.json. On Google Cloud, --out-dir is the bucket mount (gcp/).
"""

import argparse
import json
import shutil
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import joblib  # noqa: E402
import sklearn  # noqa: E402

import train_model  # noqa: E402
from canary_core import MODEL_COLUMNS  # noqa: E402

EVENTS_SQL = (
    f"SELECT time, glasses_present, {', '.join(MODEL_COLUMNS)} FROM events "
    "WHERE glasses_present IS NOT NULL "
    + "".join(f"AND {name} IS NOT NULL " for name in MODEL_COLUMNS)
    + "{demo_filter}ORDER BY time"
)


def fetch_events(url, include_demo=True):
    """Labelled events from Tiger Data as (features, label) rows."""
    import psycopg

    sql = EVENTS_SQL.format(demo_filter="" if include_demo else "AND NOT demo ")
    with psycopg.connect(url, connect_timeout=10) as conn:
        records = conn.execute(sql).fetchall()
    return events_to_rows(records)


def events_to_rows(records):
    """(time, glasses_present, *features) tuples -> (x, y). Glasses there = 1, false alarm = 0."""
    x = [[float(value) for value in record[2:]] for record in records]
    y = [int(bool(record[1])) for record in records]
    return x, y


def load_walk_folders(names=None):
    """Walks from each folder, ids prefixed with the folder name so sessions can't collide."""
    root = train_model.ROOT / "data"
    if names:
        folders = [Path(n) for n in names]
    else:
        folders = [f for f in (root / "runs_session1", root / "runs_session2") if f.is_dir()] or [root / "runs"]
    runs = {}
    for folder in folders:
        for run_id, rows in train_model.load_runs(folder).items():
            runs[f"{folder.name}/{run_id}"] = rows
    return runs, folders


def fit(walk_items, tiger_x, tiger_y, tiger_weight):
    x, y = train_model.matrix(walk_items)
    weights = [1.0] * len(y) + [tiger_weight] * len(tiger_y)
    return train_model.model_factory().fit(x + tiger_x, y + tiger_y, sample_weight=weights)


def cross_validate(runs, tiger_x, tiger_y, tiger_weight):
    """Walks-only vs walks+Tiger on the same held-out walks. Returns {name: totals}."""
    totals = {"walks only": defaultdict(float), "walks + Tiger": defaultdict(float)}
    all_ids = set(runs)
    for fold_index, test_ids in enumerate(train_model.balanced_group_folds(runs), 1):
        train_items = [(run_id, runs[run_id]) for run_id in sorted(all_ids - set(test_ids))]
        test_items = [(run_id, runs[run_id]) for run_id in test_ids]
        line = [f"Fold {fold_index}:"]
        for name, (ex, ey) in (("walks only", ([], [])), ("walks + Tiger", (tiger_x, tiger_y))):
            metrics = train_model.event_metrics(test_items, fit(train_items, ex, ey, tiger_weight))
            for key, value in metrics.items():
                totals[name][key] += value
            line.append(f"{name} ML hits {metrics['ml_hit']}/{metrics['positive_runs']}, "
                        f"false {metrics['ml_false']};")
        print(" ".join(line))
    for name, t in totals.items():
        print(f"{name:>14}: linger detected {int(t['ml_hit'])}/{int(t['positive_runs'])}, "
              f"negative walks with an alert {int(t['ml_false'])}, "
              f"premature {int(t['ml_premature'])}")
    t = totals["walks only"]
    print(f"{'3 s rule':>14}: linger detected {int(t['rule_hit'])}/{int(t['positive_runs'])}, "
          f"negative walks with an alert {int(t['rule_false'])}, premature {int(t['rule_premature'])}")
    return {name: dict(t) for name, t in totals.items()}


def save(model, info, root):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    models = root / "models"
    models.mkdir(parents=True, exist_ok=True)
    versioned = models / f"model-{stamp}.joblib"
    joblib.dump(model, versioned)
    # Write next to the target, then rename: canary.py never sees a half-written model.
    tmp = root / "model.joblib.tmp"
    shutil.copyfile(versioned, tmp)
    tmp.replace(root / "model.joblib")
    info = {**info, "model": versioned.name, "trained_at": stamp}
    (models / "latest.json").write_text(json.dumps(info, indent=2) + "\n")
    print(f"Saved {root / 'model.joblib'} (copy: {versioned})")
    return versioned


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", nargs="+",
                        help="walk folders (default: data/runs_session1 and data/runs_session2, else data/runs)")
    parser.add_argument("--tiger-weight", type=float, default=1.0, help="sample weight of Tiger rows (default 1)")
    parser.add_argument("--no-demo", action="store_true", help="skip events from the phone stand-in")
    parser.add_argument("--min-events", type=int, default=1,
                        help="exit without a new model if fewer labelled Tiger events (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="report only, don't write a model")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="where to write model.joblib and models/ (default: repo root)")
    args = parser.parse_args(argv)

    from alarm_store import service_url

    url = service_url()
    if not url:
        raise SystemExit("No TIMESCALE_SERVICE_URL found (.env, $SIDE_EYE_ENV, or the environment).")
    tiger_x, tiger_y = fetch_events(url, include_demo=not args.no_demo)
    positives = sum(tiger_y)
    print(f"Tiger Data: {len(tiger_y)} labelled events ({positives} glasses there, "
          f"{len(tiger_y) - positives} false alarms); weight {args.tiger_weight}")
    if len(tiger_y) < args.min_events:
        print(f"Fewer than {args.min_events} labelled events: keeping the current model.")
        return 0

    runs, folders = load_walk_folders(args.runs)
    print(f"Walks: {len(runs)} in {', '.join(str(f) for f in folders)}")
    info = {"tiger_events": len(tiger_y), "tiger_positive": positives, "tiger_weight": args.tiger_weight,
            "include_demo": not args.no_demo, "walks": len(runs), "sklearn": sklearn.__version__,
            "features": list(MODEL_COLUMNS)}
    if runs:
        info["cv"] = cross_validate(runs, tiger_x, tiger_y, args.tiger_weight)
    else:
        print("No walks: training on Tiger events only, with no held-out evaluation.")
    labels = [int(row["label"]) for rows in runs.values() for row in rows] + tiger_y
    if len(set(labels)) < 2:
        print("Training data has only one class (need both glasses-there and no-glasses rows): "
              "keeping the current model.")
        return 0
    model = fit(list(runs.items()), tiger_x, tiger_y, args.tiger_weight)
    if args.dry_run:
        print("Dry run: no model written.")
        return 0
    save(model, info, args.out_dir or train_model.ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
