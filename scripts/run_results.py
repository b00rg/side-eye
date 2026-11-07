"""Run the whole results pipeline on the walks in data/runs/ in one go.

Usage (from the repo root, after the walks and settings.json are in place):
  python3 scripts/run_results.py [--skip-ablations] [--seeds 0,1,2]

Reads the cutoff from settings.json (threshold_dbm) and the reading rate from the walks
(median packet_rate_hz), checks the walk mix, then runs, in order:
  1. pi/train_model.py                    Lili's training printout (T14), saves model.joblib
  2. nist/simulate_walks.py               300 simulated walks, seed 0 (T23, as pre-registered)
  3. nist/compare_models.py               main table, pre-registered tests, sweeps (T18, T24)
  4. nist/sim_vs_real.py                  can a classifier tell simulated from real walks?
  5. nist/det_curve.py                    miss vs false-alarm chart, TC4TL nDCF
  6. nist/ablations.py                    learning curve and ablations (skip with --skip-ablations)
  7. the T25 decision, using the rule in docs/PREREGISTRATION.md
Everything goes to results/ (gitignored), including results/log.txt with the full output.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "data", "runs")
OUT = os.path.join(ROOT, "results")
EXPECTED = {"linger": 8, "passer": 8, "far": 4, "bag": 2, "blocked": 2}
KINDS = list(EXPECTED)


def p(*parts):
    return os.path.join(ROOT, *parts)


class Log:
    def __init__(self, path):
        self.f = open(path, "w")

    def __call__(self, text=""):
        print(text, flush=True)
        self.f.write(text + "\n")
        self.f.flush()


def run(log, title, args, cwd=OUT):
    log(f"\n{'=' * 78}\n{title}\n{'=' * 78}")
    proc = subprocess.run([sys.executable, "-W", "ignore", *args], cwd=cwd, capture_output=True, text=True)
    for line in (proc.stdout + proc.stderr).rstrip().splitlines():
        log(line)
    if proc.returncode:
        log(f"\nFAILED (exit {proc.returncode}): {title}. Stopping here; see the error above.")
        sys.exit(proc.returncode)


def check_inputs(log):
    if not os.path.exists(p("settings.json")):
        sys.exit("No settings.json at the repo root. Copy it off the Pi first (see data/README.md).")
    settings = json.load(open(p("settings.json")))
    cutoff = float(settings["threshold_dbm"])
    files = sorted(glob.glob(os.path.join(RUNS, "*.csv")))
    if not files:
        sys.exit("No walks in data/runs/. Copy them off the Pi first (see data/README.md).")
    kinds = pd.Series([next((k for k in KINDS if k in os.path.basename(f)), "unknown") for f in files]).value_counts()
    rate = round(float(pd.concat(pd.read_csv(f, usecols=["packet_rate_hz"]) for f in files).packet_rate_hz.median()), 2)

    log("Inputs")
    log(f"  settings.json: 2 m {settings.get('near_median')} dBm, 6 m {settings.get('far_median')} dBm, cutoff {cutoff} dBm")
    log(f"  walks: {len(files)} in data/runs/  " + ", ".join(f"{k} {kinds.get(k, 0)}" for k in KINDS)
        + (f", unknown {kinds['unknown']}" if "unknown" in kinds else ""))
    log(f"  reading rate: median packet_rate_hz = {rate}/s")
    if os.path.exists(os.path.join(RUNS, "PLACEHOLDER")):
        log("  WARNING: data/runs/PLACEHOLDER exists: these are placeholder walks. Don't report these numbers.")
    wrong = {k: (kinds.get(k, 0), n) for k, n in EXPECTED.items() if kinds.get(k, 0) != n}
    if wrong or "unknown" in kinds:
        log("  WARNING: walk mix differs from the plan (8 linger, 8 passer, 4 far, 2 bag, 2 blocked): "
            + ", ".join(f"{k} {have} (expected {want})" for k, (have, want) in wrong.items())
            + ". Check data/runs/ for leftover or missing files before trusting results.")
    return cutoff, rate


def t25_decision(log):
    """Pre-registered: a model replaces the rule only if it gets more walks correct than the rule
    AND has no more false-alarm walks. Among models that qualify, pick the most walks correct."""
    t = pd.read_csv(os.path.join(OUT, "comparison_results.csv")).set_index("method")
    num = lambda s: int(str(s).split("/")[0])
    rule = t.loc["Rule (3 s)"]
    log(f"\n{'=' * 78}\nT25: what drives the motor (rule from docs/PREREGISTRATION.md)\n{'=' * 78}")
    log(f"  Rule (3 s): {rule.walks_correct} walks correct, {rule.false_alarm_walks} false-alarm walks")
    ok = []
    for m in [i for i in t.index if i.startswith("Model")]:
        r = t.loc[m]
        better = num(r.walks_correct) > num(rule.walks_correct)
        no_more_fa = num(r.false_alarm_walks) <= num(rule.false_alarm_walks)
        log(f"  {m}: {r.walks_correct} correct ({'more' if better else 'not more'} than the rule), "
            f"{r.false_alarm_walks} false alarms ({'no more' if no_more_fa else 'more'} than the rule) -> "
            f"{'qualifies' if better and no_more_fa else 'does not qualify'}")
        if better and no_more_fa:
            ok.append((num(r.walks_correct), m))
    if ok:
        log(f"\n  DECISION: {max(ok)[1]} drives the motor (most walks correct among models that qualify).")
    else:
        log("\n  DECISION: keep the 3-second rule; no model qualifies. Say so in the pitch.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ablations", action="store_true")
    ap.add_argument("--seeds", default="0", help="simulation seeds for the ablations, e.g. 0,1,2")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    log = Log(os.path.join(OUT, "log.txt"))
    cutoff, rate = check_inputs(log)
    sim = p("nist", "sim")

    run(log, "1. Lili's training printout (pi/train_model.py)", [p("pi", "train_model.py")], cwd=ROOT)
    run(log, "2. Simulate 300 walks (seed 0)", [p("nist", "simulate_walks.py"), p("data", "external", "mitre_rssi.csv"),
                                                p("settings.json"), sim, "--n", "300", "--rate", str(rate), "--seed", "0"])
    run(log, "3. Rule vs Models 1-3, pre-registered tests, sweeps",
        [p("nist", "compare_models.py"), "--sim", sim, "--real", RUNS, "--cutoff", str(cutoff)])
    run(log, "4. Fake-vs-real check", [p("nist", "sim_vs_real.py"), "--sim", sim, "--real", RUNS, "--cutoff", str(cutoff)])
    run(log, "5. Miss vs false-alarm chart and TC4TL nDCF",
        [p("nist", "det_curve.py"), "--sim", sim, "--real", RUNS, "--cutoff", str(cutoff),
         "--out", os.path.join(OUT, "det_curve.png")])
    if not args.skip_ablations:
        run(log, "6. Ablations and learning curve (exploratory; takes a minute or two per seed)",
            [p("nist", "ablations.py"), "--mitre", p("data", "external", "mitre_rssi.csv"), "--settings", p("settings.json"),
             "--real", RUNS, "--cutoff", str(cutoff), "--rate", str(rate), "--seeds", args.seeds])
    t25_decision(log)
    log(f"\nAll outputs are in {OUT}/ (tables as CSV, det_curve.png, log.txt).")


if __name__ == "__main__":
    main()
