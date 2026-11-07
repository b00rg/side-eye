"""Can a classifier tell simulated walks from real ones? (exploratory, docs/PREREGISTRATION.md)

Usage (from the repo root):
  python3 nist/sim_vs_real.py --sim nist/sim --real data/runs --cutoff <threshold_dbm>

If the simulation is realistic, a classifier given the four model inputs cannot separate
simulated rows from real rows: AUC near 0.5. AUC near 1.0 means the fakes are easy to spot,
and the per-feature AUCs and per-kind medians show which input gives them away.

AUC is measured on held-out walks (grouped 5-fold cross-validation by walk), so a classifier
can't score well just by recognising walks it has already seen.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare_models import FEATS, load_real, load_sim  # noqa: E402


def heldout_auc(df, seed=0):
    """AUC of a sim-vs-real classifier, scored on walks it never trained on."""
    scores = np.zeros(len(df))
    for train, test in GroupKFold(n_splits=5).split(df, groups=df.walk):
        clf = RandomForestClassifier(n_estimators=200, max_depth=6, class_weight="balanced", random_state=seed)
        clf.fit(df.iloc[train][FEATS], df.iloc[train].is_sim)
        scores[test] = clf.predict_proba(df.iloc[test][FEATS])[:, 1]
    return roc_auc_score(df.is_sim, scores)


def single_feature_auc(df, feat):
    """How well one feature alone separates sim from real (0.5 = not at all; folded so >= 0.5)."""
    a = roc_auc_score(df.is_sim, df[feat])
    return max(a, 1 - a)


def verdict(auc):
    if auc < 0.6:
        return "hard to tell apart: the simulation looks realistic on these inputs"
    if auc < 0.75:
        return "somewhat distinguishable: check the features with the highest AUC"
    return "easy to tell apart: the simulation differs from real walks; fix before trusting Models 2-3"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--real", required=True)
    ap.add_argument("--cutoff", type=float, required=True)
    args = ap.parse_args()

    sim = load_sim(args.sim, args.cutoff).assign(is_sim=1)
    real = load_real(args.real).assign(is_sim=0)
    df = pd.concat([sim, real], ignore_index=True).dropna(subset=FEATS)
    df = df[df.kind.isin(["passer", "linger", "far", "bag", "blocked"])]

    auc = heldout_auc(df)
    print(f"{sim.walk.nunique()} simulated walks ({len(sim)} rows), {real.walk.nunique()} real walks ({len(real)} rows)\n")
    print(f"Sim-vs-real classifier AUC on held-out walks: {auc:.2f}  ->  {verdict(auc)}\n")

    feat = pd.DataFrame([dict(feature=f, auc_alone=round(single_feature_auc(df, f), 2),
                              sim_median=round(df[df.is_sim == 1][f].median(), 2),
                              real_median=round(df[df.is_sim == 0][f].median(), 2)) for f in FEATS])
    print("Each input on its own (0.5 = gives nothing away):")
    print(feat.sort_values("auc_alone", ascending=False).to_string(index=False))

    kinds = (df.groupby(["kind", "is_sim"])[FEATS].median().round(2)
               .rename(index={0: "real", 1: "sim"}, level=1).unstack("is_sim"))
    kinds.columns = [f"{f}_{src}" for f, src in kinds.columns]
    print("\nMedian of each input by walk type, sim vs real:")
    print(kinds.to_string())

    out = pd.concat([feat.assign(section="feature"),
                     pd.DataFrame([dict(section="overall", feature="all four", auc_alone=round(auc, 2))])])
    out.to_csv("sim_vs_real.csv", index=False)
    print("\nSaved sim_vs_real.csv")


if __name__ == "__main__":
    main()
