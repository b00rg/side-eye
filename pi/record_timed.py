"""Timed recording session: all walks with countdowns instead of Enter presses.

Usage (on the Pi, in the folder with Lili's canary.py, after calibrating):
  python3 record_timed.py                 # all 24 walks in canary.WALK_PLAN order
  python3 record_timed.py --gap 15        # longer pause between walks to reposition

Reuses Lili's canary.py (scanner, Session, labels, CSV format, where files are saved) and only
replaces the Enter presses with timers:
  passer           GO, then stops by itself after PASSER_S
  far/bag/blocked  GO, then stops by itself after their hold time
  linger           GO, then ONE Enter press the instant the walker stops (that moment is the label),
                   then a HOLD_S countdown, "LEAVE NOW" (recorded automatically), and stops RETURN_S later
Between walks: a countdown to reposition, then the next walk starts itself. Ctrl+C pauses; walks
already saved stay saved. A beep (terminal bell) marks GO, LEAVE NOW and the end of each walk.
Labels are exactly as with record-all: linger seconds from stop + 3 s until leaving are 1.
"""
import argparse
import asyncio
import csv
import time
from datetime import datetime

import canary
from canary import MODEL_COLUMNS, RUNS, RecordAborted, Session, ask, new_scanner, threshold

PASSER_S = 14
HOLD_S = 20          # linger: seconds standing still after the stop press
RETURN_S = 7         # linger: seconds to walk back after LEAVE NOW
FIXED_S = {"far": 25, "bag": 30, "blocked": 30}
WARMUP_S = 5         # scanner warm-up before GO (as in canary.py)
BEEP = "\a"

SAY = {
    "passer": "PASSER: start 6 m to the side, walk straight past the Pi (1.5 m away) at a normal pace to 6 m beyond.",
    "linger": "LINGER: start on the 6 m mark, walk to the 2 m mark, STOP facing the Pi; wait for LEAVE NOW, walk back to 6 m.",
    "far": "FAR: stand still on the 4 m mark, facing the Pi.",
    "bag": "BAG: transmitter (on, with its power bank) in the bag on the chair at 2 m; everyone steps away.",
    "blocked": "BLOCKED: walker on the 2 m mark; second person on the 0.5 m mark between them and the Pi.",
}


async def countdown(seconds, message):
    for left in range(int(seconds), 0, -1):
        print(f"\r    {message} {left:2d} s ", end="", flush=True)
        await asyncio.sleep(1)
    print(f"\r    {message}  0 s ", flush=True)


async def record_timed(scenario, header):
    limit = threshold()
    session = Session()
    rows, state = [], {"recording": False, "stopped_at": None, "leaving_at": None}
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + scenario

    async def collect():   # same rows and labels as canary.py's record
        while True:
            await asyncio.sleep(1)
            if not state["recording"]:
                continue
            now = time.monotonic()
            for _address, snap in session.snapshots(now, limit):
                label = int(scenario == "linger" and state["stopped_at"] is not None
                            and now >= state["stopped_at"] + 3 and state["leaving_at"] is None)
                rows.append({"run_id": run_id, "scenario": scenario, "t": round(now, 3),
                             **{name: round(getattr(snap, name), 4) for name in MODEL_COLUMNS},
                             "close_streak_s": round(snap.close_streak_s, 4),
                             "packet_rate_hz": round(snap.packet_rate_hz, 4), "label": label})

    print(f"\n{header}\n    {SAY[scenario]}")
    async with new_scanner(session.callback):
        task = asyncio.create_task(collect())
        try:
            await countdown(WARMUP_S, "warming up, walker at START...")
            state["recording"] = True
            print(f"{BEEP}    >>> GO <<<", flush=True)
            if scenario == "linger":
                await ask("    press Enter the INSTANT the walker STOPS: ")
                state["stopped_at"] = time.monotonic()
                await countdown(HOLD_S, "hold still...")
                state["leaving_at"] = time.monotonic()
                print(f"{BEEP}    >>> LEAVE NOW <<<  walk back to 6 m", flush=True)
                await countdown(RETURN_S, "walking back...")
            else:
                await countdown(PASSER_S if scenario == "passer" else FIXED_S[scenario], "recording...")
            state["recording"] = False
            print(f"{BEEP}    done", flush=True)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    if not rows:
        raise RecordAborted("No signal captured: is the transmitter on?")
    if scenario == "linger" and not any(r["label"] for r in rows):
        raise RecordAborted("No positive rows: the stop press came too late or the signal dropped.")
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{run_id}.csv"
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path, len(rows), sum(r["label"] for r in rows)


async def main(gap):
    plan = [s for s, n in canary.WALK_PLAN for _ in range(n)]
    existing = len(list(RUNS.glob("*.csv"))) if RUNS.exists() else 0
    print(f"TIMED RECORDING: {len(plan)} walks, saving to {RUNS} (cutoff {threshold()} dBm). Ctrl+C pauses.")
    if existing:
        print(f"NOTE: {existing} CSV file(s) already there; new walks are ADDED. Ctrl+C now if that's wrong.")
        await countdown(8, "starting in")
    for i, scenario in enumerate(plan, 1):
        if i <= existing:   # resume: walks already saved in this folder count as done
            continue
        while True:
            await countdown(gap, f"NEXT: walk {i}/{len(plan)} {scenario.upper()}, get into position...")
            try:
                path, n, pos = await record_timed(scenario, f"### WALK {i}/{len(plan)}: {scenario.upper()} ###")
            except RecordAborted as exc:
                print(f"\n[FAILED] {exc} Redoing this walk.")
                continue
            print(f"[OK] walk {i}/{len(plan)} saved: {n} rows, {pos} positive -> {path.name}")
            break
    print(f"\nSESSION COMPLETE: {len(list(RUNS.glob('*.csv')))} CSV files in {RUNS}.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=12, help="seconds between walks to reposition (default 12)")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.gap))
    except KeyboardInterrupt:
        print("\nPaused. Saved walks are kept; run again to continue from the next walk.")
