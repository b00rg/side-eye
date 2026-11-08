"""Run the whole results pipeline on one folder of real walks in one go.

Usage (from the repo root):
  python3 scripts/run_results.py --runs data/runs_session1 [--skip-ablations] [--seeds 0,1,2]

Reads the cutoff from <runs>/settings.json if present, else the repo's settings.json (threshold_dbm) and the reading rate from the walks
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
RUNS = None   # set from --runs
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
    path = os.path.join(RUNS, "settings.json") if os.path.exists(os.path.join(RUNS, "settings.json")) else p("settings.json")
    if not os.path.exists(path):
        sys.exit("No settings.json in the walk folder or at the repo root.")
    settings = json.load(open(path))
    cutoff = float(settings["threshold_dbm"])
    files = sorted(glob.glob(os.path.join(RUNS, "*.csv")))
    if not files:
        sys.exit(f"No walks in {RUNS}. Copy them off the Pi first (see data/README.md).")
    kinds = pd.Series([next((k for k in KINDS if k in os.path.basename(f)), "unknown") for f in files]).value_counts()
    rate = round(float(pd.concat(pd.read_csv(f, usecols=["packet_rate_hz"]) for f in files).packet_rate_hz.median()), 2)

    log("Inputs")
    log(f"  settings.json: 2 m {settings.get('near_median')} dBm, 6 m {settings.get('far_median')} dBm, cutoff {cutoff} dBm")
    log(f"  walks: {len(files)} in {os.path.relpath(RUNS, ROOT)}/  " + ", ".join(f"{k} {kinds.get(k, 0)}" for k in KINDS)
        + (f", unknown {kinds['unknown']}" if "unknown" in kinds else ""))
    log(f"  reading rate: median packet_rate_hz = {rate}/s")
    wrong = {k: (kinds.get(k, 0), n) for k, n in EXPECTED.items() if kinds.get(k, 0) != n}
    if wrong or "unknown" in kinds:
        log("  WARNING: walk mix differs from the plan (8 linger, 8 passer, 4 far, 2 bag, 2 blocked): "
            + ", ".join(f"{k} {have} (expected {want})" for k, (have, want) in wrong.items())
            + f". Check {RUNS} for leftover or missing files before trusting results.")
    return cutoff, rate


def t25_decision(log):
    """docs/PREREGISTRATION.md, Amendment 3: Model 3 drives the motor only if it (a) catches at least
    4 of 8 lingers on time, (b) has TC4TL nDCF < 1.0, and (c) has lower nDCF than the rule."""
    t = pd.read_csv(os.path.join(OUT, "comparison_results.csv")).set_index("method")
    frac = lambda s: tuple(int(x) for x in str(s).split("/"))

    def ndcf(row):
        (hit, lingers), (fa, negatives) = frac(row.linger_detected), frac(row.false_alarm_walks)
        return (1 - hit / lingers) + fa / negatives
    log(f"\n{'=' * 78}\nT25: what drives the motor (docs/PREREGISTRATION.md, Amendment 3)\n{'=' * 78}")
    for name in t.index:
        r = t.loc[name]
        log(f"  {name:20s} lingers {r.linger_detected:>4}, false alarms {r.false_alarm_walks:>6}, nDCF {ndcf(r):.2f}")
    m3, rule = t.loc["Model 3: sim + real"], t.loc["Rule (3 s)"]
    checks = [("catches >= 4/8 lingers", frac(m3.linger_detected)[0] >= 4),
              ("nDCF < 1.0", ndcf(m3) < 1.0),
              ("nDCF lower than the rule's", ndcf(m3) < ndcf(rule))]
    for label, ok in checks:
        log(f"  Model 3 {label}: {'yes' if ok else 'NO'}")
    if all(ok for _, ok in checks):
        log("  DECISION: Model 3 drives the motor. Next: python3 scripts/deploy_model3.py")
    else:
        log("  DECISION: keep the 3-second rule; Model 3 did not clear the bar. Say so in the pitch.")
    return all(ok for _, ok in checks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-ablations", action="store_true")
    ap.add_argument("--runs", required=True, help="folder of walk CSVs, e.g. data/runs_session1")
    ap.add_argument("--seeds", default="0", help="simulation seeds for the ablations, e.g. 0,1,2")
    args = ap.parse_args()
    global RUNS
    RUNS = os.path.abspath(args.runs)
    os.makedirs(OUT, exist_ok=True)
    log = Log(os.path.join(OUT, "log.txt"))
    cutoff, rate = check_inputs(log)
    sim = p("nist", "sim")
    anchor = p("nist", "sim_anchor.json")
    anchor_args = ["--anchor", anchor] if os.path.exists(anchor) else []
    log(f"  simulation anchored to: {'nist/sim_anchor.json (session 1 walks)' if anchor_args else 'calibration medians'}")

    run(log, "1. Lili's training printout (pi/train_model.py)", [p("pi", "train_model.py"), "--runs", RUNS], cwd=ROOT)
    run(log, "2. Simulate 300 walks (seed 0)", [p("nist", "simulate_walks.py"), p("data", "external", "mitre_rssi.csv"),
                                                p("settings.json"), sim, "--n", "300", "--rate", str(rate), "--seed", "0", *anchor_args])
    run(log, "3. Rule vs Models 1-3, pre-registered tests, sweeps",
        [p("nist", "compare_models.py"), "--sim", sim, "--real", RUNS, "--cutoff", str(cutoff)])
    run(log, "4. Fake-vs-real check", [p("nist", "sim_vs_real.py"), "--sim", sim, "--real", RUNS, "--cutoff", str(cutoff)])
    run(log, "5. Miss vs false-alarm chart and TC4TL nDCF",
        [p("nist", "det_curve.py"), "--sim", sim, "--real", RUNS, "--cutoff", str(cutoff),
         "--out", os.path.join(OUT, "det_curve.png")])
    if not args.skip_ablations:
        run(log, "6. Ablations and learning curve (exploratory; takes a minute or two per seed)",
            [p("nist", "ablations.py"), "--mitre", p("data", "external", "mitre_rssi.csv"), "--settings", p("settings.json"),
             "--real", RUNS, "--cutoff", str(cutoff), "--rate", str(rate), "--seeds", args.seeds, *anchor_args])
    t25_decision(log)
    log(f"\nAll outputs are in {OUT}/ (tables as CSV, det_curve.png, log.txt).")


if __name__ == "__main__":
    main()
