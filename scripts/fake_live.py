"""Run the Pi's real `canary.py live --use-model` loop on a laptop, with a fake radio and button.

Usage (from the repo root, after scripts/deploy_model3.py has made data/sim_rows.csv):
  python3 scripts/fake_live.py

Builds a throwaway "Pi folder" like /home/canary/canary (code, settings.json, data/runs,
sim_rows.csv), trains Model 3 there, then runs the unmodified live() with only three things
swapped: the Bluetooth scanner replays simulated walks' raw readings, the clock runs fast, and
the feedback button is pressed a few seconds into each linger. Shows alerts, feedback being
saved, background retraining after 20 new rows, and live picking up the new model.
"""
import asyncio
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
PRESS_AFTER_STOP_S = 4     # the wearer presses the button 4 s after someone stops
GAP_S = 20                 # quiet seconds between walks
SESSION = ["linger", "passer", "linger", "bag", "linger", "passer", "far", "linger"]


def make_pi_folder(root):
    for name in ("canary.py", "canary_core.py", "train_model.py", "feedback.py"):
        shutil.copy(REPO / "pi" / name, root / name)
    shutil.copy(REPO / "settings.json", root / "settings.json")
    shutil.copy(REPO / "data" / "sim_rows.csv", root / "sim_rows.csv")
    (root / "data").mkdir()
    shutil.copytree(REPO / "data" / "runs", root / "data" / "runs")
    out = subprocess.run([sys.executable, "-W", "ignore", "train_model.py", "--sim-rows", "sim_rows.csv"],
                         cwd=root, capture_output=True, text=True, check=True).stdout
    print("Pi folder ready; trained:", out.strip().splitlines()[-1].split("/")[-1])


def session_readings():
    """Concatenate raw readings of simulated walks (nist/sim) into one timeline, with press times."""
    readings, presses, t0 = [], [], 0.0
    for kind in SESSION:
        lab = sorted(glob.glob(str(REPO / "nist" / "sim" / f"*_{kind}_labels.csv")))[len(readings) % 7]
        rd = pd.read_csv(lab.replace("_labels", "_readings"))
        lb = pd.read_csv(lab)
        readings += [(t0 + t, int(r)) for t, r in zip(rd.t_s, rd.rssi)]
        if kind == "linger":
            stop = lb.second[lb.label == 1].iloc[0] - 3
            presses.append(t0 + stop + PRESS_AFTER_STOP_S)
        t0 += len(lb) + GAP_S
    return readings, presses, t0


def main():
    root = Path(tempfile.mkdtemp(prefix="side-eye-pi-"))
    make_pi_folder(root)
    sys.path.insert(0, str(root))
    os.chdir(root)
    import canary  # the Pi's own code, from the Pi folder

    readings, press_times, end = session_readings()
    clock = {"t": 0.0}
    state = {"callback": None, "i": 0}
    presses = __import__("queue").SimpleQueue()
    pending_presses = list(press_times)

    class FakeScanner:
        def __init__(self, callback):
            state["callback"] = callback

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        target = clock["t"] + seconds
        while state["i"] < len(readings) and readings[state["i"]][0] <= target:
            t, rssi = readings[state["i"]]
            clock["t"] = t
            adv = SimpleNamespace(manufacturer_data={0x058E: bytes.fromhex("CA01")}, service_uuids=[], rssi=rssi)
            state["callback"](SimpleNamespace(address="AA:BB:CC:DD:EE:FF"), adv)
            state["i"] += 1
        clock["t"] = target
        while pending_presses and pending_presses[0] <= target:
            presses.put("button")
            pending_presses.pop(0)
        if clock["t"] > end + 40:
            raise KeyboardInterrupt
        await real_sleep(0)

    canary.new_scanner = FakeScanner
    canary.time = SimpleNamespace(monotonic=lambda: clock["t"])
    canary.asyncio.sleep = fast_sleep
    canary.start_presses = lambda use_button: (presses, None)
    real_retrain = canary.retrain_in_background

    def retrain_and_wait(path):   # the fake clock doesn't wait for the background job, so we do
        proc = real_retrain(path)
        proc.wait()
        time.sleep(1.1)          # make sure model.joblib gets a new modification time
        return proc
    canary.retrain_in_background = retrain_and_wait

    print(f"Session: {', '.join(SESSION)} ({len(readings)} readings, {end / 60:.1f} min of fake time)\n")
    interesting = ("ALERT", "Feedback", "retrain", "Model updated", "Side Eye live", "feedback rows so far", "No model")
    real_print = print
    import builtins

    def quiet_print(*args, **kw):
        text = " ".join(str(a) for a in args)
        if any(k in text for k in interesting):
            real_print(f"[{clock['t']:6.0f}s] {text}", **{k: v for k, v in kw.items() if k != "flush"})
    builtins.print = quiet_print
    try:
        asyncio.run(canary.live(with_motor=False, use_model=True, feedback_on=True))
    except KeyboardInterrupt:
        pass
    finally:
        builtins.print = real_print

    from feedback import FeedbackStore
    store = FeedbackStore(root / "feedback.db")
    counts = store.db.execute("SELECT source, label, COUNT(*) FROM feedback_rows GROUP BY source, label").fetchall()
    print("\nfeedback.db on the 'Pi':", ", ".join(f"{s} label {l}: {n} rows" for s, l, n in counts))
    print("retrain.log (last lines):")
    log = (root / "retrain.log").read_text().strip().splitlines() if (root / "retrain.log").exists() else []
    for line in log[-3:]:
        print("  " + line.split(str(root))[-1])
    print(f"\nPi folder left at {root} for inspection.")


if __name__ == "__main__":
    main()
