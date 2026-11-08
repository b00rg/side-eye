"""All slide figures, from real data only, into docs/figures/.

Usage (from the repo root; needs data/external/nist and data/external/h0h1 for figures 2 and 3):
  python3 scripts/figures.py            # all
  python3 scripts/figures.py 1 4 5      # some

 1 level_overlap.png     session 1: signal level by walk type overlaps; the cutoff can't separate them
 2 nist_spread.png       NIST: one reading can't tell 2 m from 6 m
 3 h0h1.png              MIT H0H1 real homes: best threshold vs model, both ~60%
 4 shape_traces.png      session 2: walk-past vs linger vs far, signal + shape-model score + alerts
 5 headline.png          shape model vs level model vs rule vs never: session 1 (exploratory) and
                         session 2 (confirmatory), with 95% intervals for session 2
 6 walk_grid.png         every session-2 walk: buzzed or not, right or wrong, shape model and rule
 7 robustness.png        session 1: all 36 shape settings tried beat never buzzing
 8 transmitter.png       readings per 20 s: Android phone vs UNO Q (scan checks)
Example walks are chosen by rule (first walk of each type in recording order), never by eye.
"""
import glob
import itertools
import json
import os
import subprocess
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nist"))
sys.path.insert(0, os.path.join(ROOT, "pi"))
from compare_models import FEATS, folds, load_real, score_walks, wilson  # noqa: E402
from shape_features import SHAPE, add_shape_all  # noqa: E402
from train_model import model_factory  # noqa: E402

OUT = os.path.join(ROOT, "docs", "figures")
S1, S2 = os.path.join(ROOT, "data", "runs_session1"), os.path.join(ROOT, "data", "runs_session2")
SURFACE, INK, INK_2, GRID, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3", "#b5b4ae"
SHAPE_C, LEVEL_C, RULE_C, NEVER_C = "#2a78d6", "#eb6834", "#1baf7a", "#8f8e88"   # palette slots 1-3 + neutral
METHODS = [("Shape model", SHAPE_C), ("Level model", LEVEL_C), ("3-second rule", RULE_C), ("Never buzz", NEVER_C)]
KIND_LABEL = {"linger": "Linger (stopped)", "passer": "Walk-past", "far": "Far (4 m)", "bag": "Bag (2 m)",
              "blocked": "Blocked (2 m)"}


# ---------------------------------------------------------------- shared helpers
def style(ax, grid="y"):
    ax.set_facecolor(SURFACE)
    if grid:
        ax.grid(axis=grid, color=GRID, lw=1)
    ax.set_axisbelow(True)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=INK_2, length=0)


def figure(w, h, ncols=1, nrows=1, **kw):
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 11})
    fig, axes = plt.subplots(nrows, ncols, figsize=(w, h), dpi=170, **kw)
    fig.patch.set_facecolor(SURFACE)
    for ax in np.atleast_1d(axes).ravel():
        style(ax)
    return fig, axes


def titles(fig, ax0, title, subtitle):
    t = fig.suptitle(title, ha="left", fontsize=15, fontweight="bold", color=INK)
    ax0.set_title(subtitle, loc="left", fontsize=10, color=INK_2, pad=12)
    return t


def save(fig, title, ax0, name):
    fig.tight_layout()
    title.set_x(ax0.get_position().x0)
    path = os.path.join(OUT, name)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    print(f"saved docs/figures/{name}")


def ndcf_of(rows):
    hit = sum(r["linger_detected"] for r in rows); lingers = sum(r["linger_total"] for r in rows)
    fa = sum(r["false_alarm_walks"] for r in rows); neg = sum(r["negative_walks"] for r in rows)
    return hit, lingers, fa, neg, (1 - hit / lingers) + fa / neg


def session_scores(pool, scored_by):
    """{method: per-walk correct dict} for one test pool already scored."""
    return {name: score_walks(df, **kw)["correct"] for name, (df, kw) in scored_by.items()}


def shape_session1_cv():
    """Exploratory: session 1, shape and level models under the pre-registered folds."""
    pool = add_shape_all(load_real(S1))
    out = {"Shape model": [], "Level model": [], "3-second rule": [], "Never buzz": []}
    for held in folds(pool):
        tr, te = pool[~pool.walk.isin(held)], pool[pool.walk.isin(held)].copy()
        for name, feats in (("Shape model", SHAPE), ("Level model", FEATS)):
            te["_score"] = model_factory().fit(tr[feats].values, tr.label.values).predict_proba(te[feats].values)[:, 1]
            out[name].append(score_walks(te, thresh=0.8))
        out["3-second rule"].append(score_walks(te, rule_wait=3))
        out["Never buzz"].append(score_walks(te.assign(_score=0.0), thresh=2.0))
    return {k: ndcf_of(v) for k, v in out.items()}


def session2_scored():
    """Confirmatory: trained on all of session 1, scored once on session 2 (as nist/shape_confirm.py)."""
    train, test = add_shape_all(load_real(S1)), add_shape_all(load_real(S2))
    scored = {}
    for name, feats in (("Shape model", SHAPE), ("Level model", FEATS)):
        t = test.copy()
        t["_score"] = model_factory().fit(train[feats].values, train.label.values).predict_proba(test[feats].values)[:, 1]
        scored[name] = (t, dict(thresh=0.8))
    scored["3-second rule"] = (test, dict(rule_wait=3))
    scored["Never buzz"] = (test.assign(_score=0.0), dict(thresh=2.0))
    return test, scored


# ---------------------------------------------------------------- 1
def fig_level_overlap():
    d = load_real(S1)
    cutoff = json.load(open(os.path.join(S1, "settings.json")))["threshold_dbm"]
    groups = [("linger", d[(d.kind == "linger") & (d.label == 1)]), *[(k, d[d.kind == k]) for k in
                                                                     ("passer", "far", "bag", "blocked")]]
    fig, ax = figure(10, 5.6)
    rng = np.random.default_rng(0)
    for i, (k, g) in enumerate(groups):
        y = np.full(len(g), i) + rng.uniform(-0.18, 0.18, len(g))
        color = SHAPE_C if k == "linger" else MUTED
        ax.scatter(g.mean_rssi, y, s=10, color=color, alpha=0.55, lw=0, zorder=2)
        q1, med, q3 = g.mean_rssi.quantile([0.25, 0.5, 0.75])
        ax.plot([q1, q3], [i, i], color=INK, lw=4, solid_capstyle="round", zorder=3)
        ax.plot(med, i, "o", ms=9, mfc=SURFACE, mec=INK, mew=2, zorder=4)
        ax.text(-71, i, f"median {med:.0f}", va="center", ha="left", fontsize=9, color=INK_2)
    ax.axvline(cutoff, color=INK_2, lw=1.5, ls=(0, (5, 4)), zorder=1)
    ax.text(cutoff + 0.3, -0.62, f"calibrated cutoff {cutoff:.1f} dBm: everything to the right counts as 'near'",
            fontsize=9, color=INK_2, va="center")
    ax.set_ylim(len(groups) - 0.5, -0.9)
    ax.set_yticks(range(len(groups)))
    ax.set_yticklabels([KIND_LABEL[k] for k, _ in groups], color=INK)
    ax.set_xlim(-72, -35)
    ax.grid(axis="x", color=GRID, lw=1)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("smoothed signal strength each second (dBm)  stronger →", color=INK_2)
    t = titles(fig, ax, "Signal strength can't tell the walk types apart",
               "Session 1, 24 real walks, one dot per second. Bar = middle 50%, circle = median.\n"
               "A stopped linger is only a few dB stronger, and every walk type sits mostly above the calibrated cutoff,\n"
               "so a strength threshold buzzes at almost everything.")
    save(fig, t, ax, "1_level_overlap.png")


# ---------------------------------------------------------------- 2
def fig_nist_spread():
    nist = os.path.join(ROOT, "data", "external", "nist", "BLE_RSSI_Data")
    if not os.path.isdir(nist):
        print("skip 2: data/external/nist missing (see nist/README.md)")
        return
    subprocess.run([sys.executable, "-W", "ignore", os.path.join(ROOT, "nist", "plot_spread.py"), nist,
                    os.path.join(OUT, "2_nist_spread.png")], check=True, stdout=subprocess.DEVNULL)
    print("saved docs/figures/2_nist_spread.png")


# ---------------------------------------------------------------- 3
def fig_h0h1():
    root = os.path.join(ROOT, "data", "external", "h0h1")
    if not os.path.isdir(root):
        print("skip 3: data/external/h0h1 missing (see nist/h0h1_eval.py)")
        return
    import h0h1_eval as h
    from sklearn.model_selection import GroupKFold
    w = h.windows(h.load(root), 10)
    label, groups, ids = w.label.to_numpy(), w.group.to_numpy(), w.group.unique()
    preds = {}
    for name, feats in (("Best single threshold", None), ("Random forest, all inputs", h.FEATS),
                        ("Random forest, no readings/s*", ["mean", "median", "var", "slope", "p10", "p90"])):
        p = np.zeros(len(w), bool)
        for tr_i, te_i in GroupKFold(5).split(w, groups=groups):
            tr, te = w.iloc[tr_i], w.iloc[te_i]
            if feats is None:
                p[te_i] = te["mean"].to_numpy() >= h.best_cutoff(tr["mean"].to_numpy(), tr.label.to_numpy())
            else:
                p[te_i] = model_factory().fit(tr[feats].to_numpy(), tr.label.to_numpy()).predict_proba(te[feats].to_numpy())[:, 1] >= 0.5
        preds[name] = p
    rng = np.random.default_rng(0)
    rows = []
    for name, p in preds.items():
        ba = h.scores(label, p)["balanced_acc"]
        boot = []
        for _ in range(1000):
            pick = rng.choice(ids, len(ids))
            idx = np.concatenate([np.flatnonzero(groups == g) for g in pick])
            if label[idx].min() != label[idx].max():
                boot.append(h.scores(label[idx], p[idx])["balanced_acc"])
        rows.append((name, ba, *np.percentile(boot, [2.5, 97.5])))
    fig, ax = figure(9.5, 4.2)
    colors = [RULE_C, LEVEL_C, LEVEL_C]
    for i, (name, ba, lo, hi) in enumerate(rows):
        ax.plot([lo, hi], [i, i], color=colors[i], lw=3, solid_capstyle="round", alpha=0.6)
        ax.plot(ba, i, "o", ms=11, mfc=colors[i], mec=SURFACE, mew=2)
        ax.text(hi + 0.012, i, f"{ba:.0%}  ({lo:.0%}-{hi:.0%})", va="center", fontsize=10, color=INK)
    ax.axvline(0.5, color=INK_2, lw=1.5, ls=(0, (5, 4)))
    ax.text(0.505, -0.42, "coin flip", fontsize=9, color=INK_2)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in rows], color=INK)
    ax.set_ylim(len(rows) - 0.5, -0.7)
    ax.set_xlim(0.35, 1.0)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.grid(axis="x", color=GRID, lw=1)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("balanced accuracy, close (< 6 ft) vs far (>= 10 ft), on held-out sessions", color=INK_2)
    t = titles(fig, ax, "Not just our room: real homes hit the same ceiling",
               "MIT Lincoln Laboratory H0H1: 17 real home sessions, iPhones in hands and pockets.\n"
               "10 s windows; 95% intervals from resampling sessions.\nNeither a threshold nor a model gets far past ~60-65%. *exploratory variant.")
    save(fig, t, ax, "3_h0h1.png")


# ---------------------------------------------------------------- 4
def fig_shape_traces():
    test, scored = session2_scored()
    shape_df = scored["Shape model"][0]
    picks = []
    for kind in ("passer", "linger", "far"):   # first walk of each type in recording order
        picks.append(sorted(shape_df[shape_df.kind == kind].walk.unique())[0])
    fig, axes = figure(14, 6.2, ncols=3, nrows=2, sharey="row", gridspec_kw={"height_ratios": [3, 2]})
    for col, walk in enumerate(picks):
        g = shape_df[shape_df.walk == walk].sort_values("t")
        t = (g.t - g.t.iloc[0]).to_numpy()
        top, bottom = axes[0, col], axes[1, col]
        kind = g.kind.iloc[0]
        pos = t[g.label.to_numpy() == 1]
        if len(pos):
            for ax in (top, bottom):
                ax.axvspan(pos[0] - 3, pos[-1], color=INK_2, alpha=0.08, lw=0)
            top.text((pos[0] - 3 + pos[-1]) / 2, -66, "stopped", ha="center", fontsize=9, color=INK_2)
        top.plot(t, g.mean_rssi, color=INK, lw=2.2)
        bottom.plot(t, g._score, color=SHAPE_C, lw=2.5)
        bottom.axhline(0.8, color=INK_2, lw=1.2, ls=(0, (5, 4)))
        from compare_models import walk_alerts
        first = walk_alerts(g._score.to_numpy(), t)
        rule_hit = np.flatnonzero(g.close_streak_s.to_numpy() >= 3)
        rule_first = t[rule_hit[0]] if len(rule_hit) else None
        if first is not None:
            for ax in (top, bottom):
                ax.axvline(first, color=SHAPE_C, lw=2.5)
            top.text(first + 0.4, -36.5, "shape model\nbuzzes", color=SHAPE_C, fontsize=9, fontweight="bold", va="top")
        if rule_first is not None:
            for ax in (top, bottom):
                ax.axvline(rule_first, color=RULE_C, lw=1.6, ls=":")
            left = first is not None and 0 < first - rule_first < 4   # shape line just after: label on the left
            top.text(rule_first + (-0.4 if left else 0.4), -68.5, "3 s rule\nwould buzz", color=INK_2, fontsize=8,
                     va="bottom", ha="right" if left else "left")
        verdict = {"passer": "should stay quiet", "linger": "should buzz", "far": "should stay quiet"}[kind]
        if kind == "linger":   # same scoring as compare_models: an alert before the labels start is too early
            outcome = ("missed" if first is None else
                       "buzzed before the stop (too early, a miss)" if first < pos[0] else "caught")
        else:
            outcome = "false alarm" if first is not None else "stayed quiet"
        top.set_title(f"{KIND_LABEL[kind].split(' (')[0]}: {verdict}\nShape model: {outcome}", loc="left",
                      fontsize=11, color=INK)
        top.set_ylim(-70, -35)
        bottom.set_ylim(-0.03, 1.03)
        bottom.set_xlabel("seconds", color=INK_2)
    axes[0, 0].set_ylabel("smoothed signal (dBm)", color=INK_2)
    axes[1, 0].set_ylabel("shape-model score", color=INK_2)
    axes[1, 0].text(0.02, 0.83, "alert level 0.8", transform=axes[1, 0].transAxes, fontsize=8, color=INK_2)
    tt = fig.suptitle("Shape, not strength: how the signal moves separates walking past from lingering", ha="left",
                      fontsize=15, fontweight="bold", color=INK)
    fig.text(0, 0, "")
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    tt.set_x(axes[0, 0].get_position().x0)
    fig.text(axes[0, 0].get_position().x0, 0.895, "Session 2 (new walks; model trained on session 1). First walk of each type by name, chosen by rule, "
             "not for looks:\nhere the shape model is right on 1 of 3 (it went 12/16 across all session-2 walks). "
             "The 3-second rule buzzes on all three.",
             fontsize=10, color=INK_2)
    fig.savefig(os.path.join(OUT, "4_shape_traces.png"), facecolor=SURFACE)
    plt.close(fig)
    print("saved docs/figures/4_shape_traces.png")


# ---------------------------------------------------------------- 5
def fig_headline():
    s1 = shape_session1_cv()
    test, scored = session2_scored()
    kinds = test.groupby("walk").kind.first()
    rng = np.random.default_rng(0)
    lingers = kinds[kinds == "linger"].index.to_numpy()
    others = kinds[kinds != "linger"].index.to_numpy()
    s2 = {}
    for name, (df, kw) in scored.items():
        correct = score_walks(df, **kw)["correct"]
        hit = sum(correct[w] for w in lingers); fa = sum(not correct[w] for w in others)
        point = (1 - hit / len(lingers)) + fa / len(others)
        boot = []
        for _ in range(2000):   # resample walks within lingers and within others
            L = rng.choice(lingers, len(lingers)); O = rng.choice(others, len(others))
            boot.append((1 - np.mean([correct[w] for w in L])) + np.mean([not correct[w] for w in O]))
        s2[name] = (hit, len(lingers), fa, len(others), point, *np.percentile(boot, [2.5, 97.5]))
    fig, ax = figure(11, 5.2)
    for i, (name, color) in enumerate(METHODS):
        h1, L1, f1, N1, d1 = s1[name]
        h2, L2, f2, N2, d2, lo, hi = s2[name]
        ax.plot(d1, i - 0.15, "o", ms=10, mfc=SURFACE, mec=color, mew=2.2, zorder=3)
        ax.plot([lo, hi], [i + 0.15, i + 0.15], color=color, lw=3, alpha=0.55, solid_capstyle="round")
        ax.plot(d2, i + 0.15, "o", ms=11, mfc=color, mec=SURFACE, mew=2, zorder=4)
        ax.text(2.02, i - 0.15, f"session 1: {h1}/{L1} lingers, {f1}/{N1} false alarms", va="center", fontsize=9, color=INK_2)
        ax.text(2.02, i + 0.15, f"session 2: {h2}/{L2} lingers, {f2}/{N2} false alarms", va="center", fontsize=9,
                color=INK, fontweight="bold" if name == "Shape model" else "normal")
    ax.axvline(1.0, color=INK_2, lw=1.5, ls=(0, (5, 4)))
    ax.text(1.02, -0.62, "no better than never buzzing", fontsize=9, color=INK_2, ha="left")
    ax.set_yticks(range(len(METHODS)))
    ax.set_yticklabels([m for m, _ in METHODS], color=INK, fontsize=11)
    ax.invert_yaxis()
    ax.set_ylim(len(METHODS) - 0.5, -0.85)
    ax.set_xlim(0, 2.0)
    ax.grid(axis="x", color=GRID, lw=1)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("misses + false alarms (TC4TL nDCF), lower is better", color=INK_2)
    t = titles(fig, ax, "The shape model held up on walks it had never seen",
               "Hollow: session 1 (24 walks, exploratory, found after seeing the data).\n"
               "Filled: session 2 (16 new walks, pre-registered, tested once), with 95% interval.\nLevel model and rule use the calibrated cutoff, which "
               "differed between sessions (-59.5 vs -53.3 dBm);\nthe shape model doesn't use it.")
    fig.tight_layout()
    fig.subplots_adjust(right=0.66)
    t.set_x(ax.get_position().x0)
    fig.savefig(os.path.join(OUT, "5_headline.png"), facecolor=SURFACE)
    plt.close(fig)
    print("saved docs/figures/5_headline.png")


# ---------------------------------------------------------------- 6
def fig_walk_grid():
    test, scored = session2_scored()
    order = []
    for kind in ("linger", "passer", "far"):
        order += sorted(test[test.kind == kind].walk.unique())
    cols = [("Shape model", scored["Shape model"]), ("3-second rule", scored["3-second rule"])]
    results = {name: score_walks(df, **kw) for name, (df, kw) in cols}
    alerted = {}
    for name, (df, kw) in cols:
        a = {}
        for w, g in df.groupby("walk"):
            g = g.sort_values("t"); t = (g.t - g.t.iloc[0]).to_numpy()
            if kw.get("rule_wait"):
                a[w] = bool((g.close_streak_s >= kw["rule_wait"]).any())
            else:
                from compare_models import walk_alerts
                a[w] = walk_alerts(g._score.to_numpy(), t, kw["thresh"]) is not None
        alerted[name] = a
    fig, ax = figure(8.6, 9.2)
    ax.grid(False)
    for r, w in enumerate(order):
        kind = test[test.walk == w].kind.iloc[0]
        ax.text(-0.1, r, f"{KIND_LABEL[kind].split(' (')[0]} {r + 1 - order.index(next(x for x in order if test[test.walk == x].kind.iloc[0] == kind))}",
                ha="right", va="center", fontsize=10, color=INK)
        for c, (name, _) in enumerate(cols):
            ok = results[name]["correct"][w]
            buzz = alerted[name][w]
            face = "#e8f2fc" if ok else "#fbe9e7"
            ax.add_patch(plt.Rectangle((c * 1.0 + 0.04, r - 0.42), 0.92, 0.84, color=face, lw=0))
            mark = "✓" if ok else "✗"
            txt = ("buzzed" if buzz else "quiet") + ("" if ok or kind != "linger" or not buzz else " (too early)")
            ax.text(c + 0.5, r, f"{mark}  {txt}", ha="center", va="center", fontsize=10,
                    color=INK, fontweight="bold" if not ok else "normal")
    for r in (5.5, 11.5):
        ax.axhline(r, color=INK_2, lw=1)
    for c, (name, _) in enumerate(cols):
        hits = sum(results[name]["correct"].values())
        ax.text(c + 0.5, -1, f"{name}\n{hits}/{len(order)} walks right", ha="center", va="bottom", fontsize=11,
                fontweight="bold", color=INK)
    ax.set_xlim(-1.3, 2.05)
    ax.set_ylim(len(order) - 0.5, -2.2)
    ax.set_xticks([]); ax.set_yticks([])
    t = fig.suptitle("Every session-2 walk, honestly", ha="left", fontsize=15, fontweight="bold", color=INK)
    fig.text(0.04, 0.93, "16 new walks, model trained on session 1. ✓ = right (linger buzzed on time, others stayed quiet),\n"
             "✗ = wrong. The shape model gets every far walk and 5/6 lingers; half the walk-pasts fool it.",
             fontsize=10, color=INK_2, va="top")
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    t.set_x(0.04)
    fig.savefig(os.path.join(OUT, "6_walk_grid.png"), facecolor=SURFACE)
    plt.close(fig)
    print("saved docs/figures/6_walk_grid.png")


# ---------------------------------------------------------------- 7
def fig_robustness():
    from shape_features import add_shape
    base = load_real(S1)
    fl = list(folds(base))
    rows = []
    for lw, sw, rd in itertools.product((10, 15, 20, 30), (5, 8, 12), (2, 4, 6)):
        pool = pd.concat(add_shape(g, low_window=lw, steady_window=sw, rise_db=rd) for _, g in base.groupby("walk", sort=False))
        res = []
        for held in fl:
            tr, te = pool[~pool.walk.isin(held)], pool[pool.walk.isin(held)].copy()
            te["_score"] = model_factory().fit(tr[SHAPE].values, tr.label.values).predict_proba(te[SHAPE].values)[:, 1]
            res.append(score_walks(te, thresh=0.8))
        rows.append((lw, sw, rd, ndcf_of(res)[4]))
    d = pd.DataFrame(rows, columns=["low", "steady", "rise", "ndcf"]).sort_values("ndcf").reset_index(drop=True)
    fig, ax = figure(10, 4.6)
    chosen = (d.low == 20) & (d.steady == 8) & (d.rise == 4)
    ax.bar(d.index, d.ndcf, width=0.75, color=[SHAPE_C if c else "#9cc0ec" for c in chosen], zorder=3)
    ax.axhline(1.0, color=INK_2, lw=1.5, ls=(0, (5, 4)))
    ax.text(len(d) - 0.5, 1.03, "never buzzing (1.0)", ha="right", fontsize=9, color=INK_2)
    i = int(np.flatnonzero(chosen)[0])
    ax.annotate("setting fixed for the\nconfirmatory test", (i, d.ndcf[i]), xytext=(i, d.ndcf[i] + 0.35),
                ha="center", fontsize=9, color=INK, arrowprops=dict(arrowstyle="-", color=INK_2, lw=1))
    ax.set_xticks([])
    ax.set_ylim(0, 1.2)
    ax.set_xlabel("36 settings tried (window lengths 10-30 s, 5-12 s; rise 2-6 dB), sorted", color=INK_2)
    ax.set_ylabel("misses + false alarms (nDCF)", color=INK_2)
    t = titles(fig, ax, "Not cherry-picked: every setting tried beat never buzzing",
               f"Session 1, exploratory, same folds. Median {d.ndcf.median():.2f}, range {d.ndcf.min():.2f}-{d.ndcf.max():.2f}. "
               "The setting later tested on new walks was fixed in advance.")
    save(fig, t, ax, "7_robustness.png")


# ---------------------------------------------------------------- 8
def fig_transmitter():
    # Pi scan checks, 20 s each, transmitter ~1 m from the Pi (7-8 Nov 2026, as logged in the session)
    android = [27, 41, 41, 37]   # incl. the first scan (phone possibly >1 m away)
    unoq = [162, 184, 146]
    fig, ax = figure(10, 4.4)
    for i, (name, vals, color) in enumerate((("Android phone\n(nRF Connect, 100 ms set)", android, NEVER_C),
                                             ("Arduino UNO Q\n(20 ms, power bank)", unoq, SHAPE_C))):
        m = np.mean(vals)
        ax.barh(i, m, height=0.5, color=color, zorder=3)
        ax.scatter(vals, [i] * len(vals), s=40, color=INK, zorder=4, edgecolor=SURFACE, lw=1.5)
        ax.text(max(vals) + 10, i, f"mean {m:.0f} in 20 s\n(~{m / 20:.0f} per second)", va="center", fontsize=10, color=INK)
    ax.set_yticks([0, 1])
    ax.set_yticklabels([lbl for lbl in ("Android phone\n(nRF Connect, 100 ms set)", "Arduino UNO Q\n(20 ms, power bank)")],
                       color=INK)
    ax.invert_yaxis()
    ax.set_xlim(0, 250)
    ax.grid(axis="x", color=GRID, lw=1)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("readings at the Pi per 20 s scan (bar = mean, dots = individual scans)", color=INK_2)
    t = titles(fig, ax, "Swapping the phone for a small board: ~4x more readings",
               "Android sent far fewer adverts than its 100 ms setting; the UNO Q advertises every 20 ms.\n"
               "More readings per second = steadier per-second features. Few scans each, transmitter ~1 m away.")
    save(fig, t, ax, "8_transmitter.png")


FIGS = {1: fig_level_overlap, 2: fig_nist_spread, 3: fig_h0h1, 4: fig_shape_traces, 5: fig_headline,
        6: fig_walk_grid, 7: fig_robustness, 8: fig_transmitter}

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    which = [int(a) for a in sys.argv[1:]] or list(FIGS)
    for k in which:
        FIGS[k]()
