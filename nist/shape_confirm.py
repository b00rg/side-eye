"""Confirmatory test of the shape model (docs/PREREGISTRATION.md, Amendment 4).

Usage (from the repo root, after the confirmation walks are in their own folder):
  python3 nist/shape_confirm.py --train data/runs_session1 --test data/runs_session2

Trains on ALL session-1 walks, tests once on the confirmation walks (never seen, recorded after the
amendment). Same alert rule as everywhere else (score >= 0.8 twice within 2.5 s; walk-level scoring
from compare_models). Compares: the shape model, the level model (Model 1's inputs), the 3-second
rule, and never buzzing. Prints the pre-registered verdict for H4.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "pi"))
from compare_models import FEATS, load_real, score_walks, wilson  # noqa: E402
from shape_features import SHAPE, add_shape_all  # noqa: E402
from train_model import model_factory  # noqa: E402


def summary(rows):
    hit = sum(r["linger_detected"] for r in rows)
    lingers = sum(r["linger_total"] for r in rows)
    fa = sum(r["false_alarm_walks"] for r in rows)
    negatives = sum(r["negative_walks"] for r in rows)
    early = sum(r["early_alerts"] for r in rows)
    return hit, lingers, fa, negatives, early, (1 - hit / lingers) + fa / negatives


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="data/runs_session1")
    ap.add_argument("--test", default="data/runs_session2")
    args = ap.parse_args()
    train = add_shape_all(load_real(args.train))
    test = add_shape_all(load_real(args.test))
    kinds = test.groupby("walk").kind.first().value_counts().to_dict()
    print(f"Train: {train.walk.nunique()} walks ({args.train}). Test: {test.walk.nunique()} walks ({args.test}) {kinds}\n")

    results = {}
    for name, feats in (("Shape model", SHAPE), ("Level model (Model 1 inputs)", FEATS)):
        model = model_factory().fit(train[feats].values, train.label.values)
        scored = test.copy()
        scored["_score"] = model.predict_proba(test[feats].values)[:, 1]
        results[name] = summary([score_walks(scored, thresh=0.8)])
    results["3-second rule"] = summary([score_walks(test, rule_wait=3)])
    results["Never buzz"] = summary([score_walks(test.assign(_score=0.0), thresh=2.0)])

    print(f"{'':30s} {'lingers caught':>15s} {'false alarms':>13s} {'too early':>10s} {'nDCF':>6s}")
    for name, (hit, lingers, fa, neg, early, ndcf) in results.items():
        lo, hi = wilson(hit, lingers)
        print(f"{name:30s} {f'{hit}/{lingers} ({lo:.0%}-{hi:.0%})':>15s} {f'{fa}/{neg}':>13s} {early:>10d} {ndcf:6.2f}")

    hit, lingers, fa, neg, _, ndcf = results["Shape model"]
    rule_ndcf = results["3-second rule"][5]
    checks = [("nDCF below 1.0 (beats never buzzing)", ndcf < 1.0),
              ("nDCF below the 3-second rule's", ndcf < rule_ndcf),
              (f"catches at least half the lingers ({hit}/{lingers})", hit >= lingers / 2),
              (f"false alarms at most 20% of other walks ({fa}/{neg})", fa <= 0.2 * neg)]
    print("\nH4 (Amendment 4): the shape model, trained on session 1, works on new walks")
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    print("  H4 primary (first two): " + ("SUPPORTED" if checks[0][1] and checks[1][1] else "NOT SUPPORTED"))


if __name__ == "__main__":
    main()
