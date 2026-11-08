"""Train a research classifier from the team's own phone-beacon runs.

The live haptic path remains the measured 3-second proximity rule. The model's
probability is an experimental score, not a probability of surveillance.
"""

import argparse
import csv
import random
from collections import defaultdict
from pathlib import Path

import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import precision_score, recall_score

from canary_core import MODEL_COLUMNS, project_root


ROOT = project_root(__file__)  # repo root in the repo; this folder when deployed flat on the Pi


def load_runs(folder=None):
    runs = {}
    for path in sorted((Path(folder) if folder else ROOT / "data" / "runs").glob("*.csv")):
        with path.open(newline="") as source:
            rows = list(csv.DictReader(source))
        if not rows:
            continue
        if any(not all(column in row for column in (*MODEL_COLUMNS, "label", "t", "run_id", "close_streak_s")) for row in rows):
            raise SystemExit(f"Missing required columns in {path}")
        run_id = rows[0]["run_id"]
        if run_id in runs:
            raise SystemExit(f"Duplicate run ID {run_id}; rename or re-record one run")
        runs[run_id] = sorted(rows, key=lambda row: float(row["t"]))
    return runs


def model_factory():
    return RandomForestClassifier(
        n_estimators=20, max_depth=4, class_weight="balanced", random_state=42
    )


def matrix(run_items):
    rows = [row for _, run in run_items for row in run]
    x = [[float(row[name]) for name in MODEL_COLUMNS] for row in rows]
    y = [int(row["label"]) for row in rows]
    return x, y


def balanced_group_folds(runs, n_splits=4):
    """Put whole runs in folds and spread positive and negative runs."""
    classes = defaultdict(list)
    for run_id, rows in runs.items():
        classes[max(int(row["label"]) for row in rows)].append(run_id)
    if len(classes[0]) < n_splits or len(classes[1]) < n_splits:
        raise SystemExit(
            f"Need at least {n_splits} runs with positive rows and {n_splits} negative-only runs; "
            f"have {len(classes[1])} and {len(classes[0])}."
        )
    rng = random.Random(42)
    folds = [[] for _ in range(n_splits)]
    for label in (0, 1):
        ids = classes[label]
        rng.shuffle(ids)
        for i, run_id in enumerate(ids):
            folds[i % n_splits].append(run_id)
    return folds


def event_metrics(test_runs, model):
    result = {"ml_hit": 0, "rule_hit": 0, "positive_runs": 0,
              "ml_false": 0, "rule_false": 0, "ml_premature": 0,
              "rule_premature": 0, "negative_minutes": 0.0}
    for _run_id, rows in test_runs:
        x, labels = matrix([(_run_id, rows)])
        probabilities = model.predict_proba(x)[:, 1]
        ml_decisions = [float(p) >= 0.8 for p in probabilities]
        rule_decisions = [float(row["close_streak_s"]) >= 3.0 for row in rows]
        positive = any(labels)
        if positive:
            result["positive_runs"] += 1
        else:
            if len(rows) > 1:
                result["negative_minutes"] += max(0, float(rows[-1]["t"]) - float(rows[0]["t"])) / 60
        for name, decisions in (("ml", ml_decisions), ("rule", rule_decisions)):
            # Compare the two-high-score ML proposal with the live three-second
            # rule. Both get the same 15-second cooldown per recorded run.
            alarm_indices = []
            last_alarm = float("-inf")
            for i, decision in enumerate(decisions):
                candidate = decision
                if name == "ml":
                    candidate = (i > 0 and decision and decisions[i - 1]
                                 and float(rows[i]["t"]) - float(rows[i - 1]["t"]) <= 2.5)
                t = float(rows[i]["t"])
                if candidate and t - last_alarm >= 15:
                    alarm_indices.append(i)
                    last_alarm = t
            if positive and any(labels[i] for i in alarm_indices):
                result[f"{name}_hit"] += 1
            if positive and any(not labels[i] for i in alarm_indices):
                result[f"{name}_premature"] += 1
            if not positive and alarm_indices:
                result[f"{name}_false"] += 1
    return result


def load_sim_rows(path):
    """Simulated rows (MODEL_COLUMNS + label) exported by nist/export_sim_rows.py."""
    with open(path, newline="") as source:
        rows = list(csv.DictReader(source))
    x = [[float(row[name]) for name in MODEL_COLUMNS] for row in rows]
    y = [int(row["label"]) for row in rows]
    return x, y


def fit(x, y, sim=None, sim_weight=0.2, feedback=None):
    """Model 1 from real rows; Model 3 if simulated rows are given (each weighted sim_weight);
    feedback rows (from the button, with their own weights) are added when given."""
    if sim is None and feedback is None:
        return model_factory().fit(x, y)
    xs, ys, ws = list(x), list(y), [1.0] * len(x)
    if sim is not None:
        xs, ys, ws = xs + sim[0], ys + sim[1], ws + [sim_weight] * len(sim[0])
    if feedback is not None:
        xs, ys, ws = xs + feedback[0], ys + feedback[1], ws + feedback[2]
    return model_factory().fit(xs, ys, sample_weight=ws)


def main(argv=()):
    ap = argparse.ArgumentParser(description="Train and evaluate the linger model on data/runs")
    ap.add_argument("--sim-rows", help="simulated rows CSV (nist/export_sim_rows.py): trains Model 3 instead of Model 1")
    ap.add_argument("--sim-weight", type=float, default=0.2, help="weight of each simulated row (default 0.2)")
    ap.add_argument("--feedback", help="feedback.db from the button (canary.py live): adds those rows to training")
    ap.add_argument("--runs", help="folder of walk CSVs to train on (default data/runs)")
    args = ap.parse_args(list(argv))
    feedback = None
    if args.feedback and Path(args.feedback).exists():
        from feedback import load_rows

        feedback = load_rows(args.feedback)
        print(f"Plus {len(feedback[0])} feedback rows from the button ({sum(feedback[1])} 'lingering')")
    sim = None
    if args.sim_rows:
        sim_path = Path(args.sim_rows)
        sim = load_sim_rows(sim_path if sim_path.is_absolute() else ROOT / sim_path)
        print(f"Model 3: real walks + {len(sim[0])} simulated rows (weight {args.sim_weight})")
    else:
        print("Model 1: real walks only")
    runs = load_runs(args.runs)
    if not runs:
        raise SystemExit("No CSV files in data/runs. Record scenarios first.")
    folds = balanced_group_folds(runs)
    all_ids = set(runs)
    true_rows = []
    predicted_rows = []
    totals = defaultdict(float)
    for fold_index, test_ids in enumerate(folds, 1):
        train_items = [(run_id, runs[run_id]) for run_id in sorted(all_ids - set(test_ids))]
        test_items = [(run_id, runs[run_id]) for run_id in test_ids]
        x_train, y_train = matrix(train_items)
        x_test, y_test = matrix(test_items)
        model = fit(x_train, y_train, sim, args.sim_weight, feedback)
        predicted = [int(p >= 0.8) for p in model.predict_proba(x_test)[:, 1]]
        true_rows.extend(y_test)
        predicted_rows.extend(predicted)
        metrics = event_metrics(test_items, model)
        for name, value in metrics.items():
            totals[name] += value
        print(
            f"Fold {fold_index}: {len(test_ids)} held-out runs, "
            f"ML hits {metrics['ml_hit']}/{metrics['positive_runs']}, "
            f"rule hits {metrics['rule_hit']}/{metrics['positive_runs']}"
        )
    print(f"Runs: {len(runs)}; rows: {len(true_rows)}")
    print(f"ML row precision at 0.80: {precision_score(true_rows, predicted_rows, zero_division=0):.3f}")
    print(f"ML row recall at 0.80: {recall_score(true_rows, predicted_rows, zero_division=0):.3f}")
    print(f"Positive runs detected: ML {int(totals['ml_hit'])}/{int(totals['positive_runs'])}; "
          f"rule {int(totals['rule_hit'])}/{int(totals['positive_runs'])}")
    print(f"Negative runs with an alert: ML {int(totals['ml_false'])}; rule {int(totals['rule_false'])}")
    print(f"Lingering runs with a premature alert: ML {int(totals['ml_premature'])}; "
          f"rule {int(totals['rule_premature'])}")
    print(f"Negative run time: {totals['negative_minutes']:.1f} minutes")
    final_x, final_y = matrix(list(runs.items()))
    model = fit(final_x, final_y, sim, args.sim_weight, feedback)
    model_path = ROOT / "model.joblib"
    joblib.dump(model, model_path)
    print(f"Saved {'Model 3' if sim else 'Model 1'}: {model_path}")


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
