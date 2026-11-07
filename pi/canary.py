"""Canary CLI. Run `python3 pi/canary.py --help` on the Raspberry Pi."""

import argparse
import asyncio
import csv
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from canary_core import MODEL_COLUMNS, ModelAlert, Tracker, project_root, signature


ROOT = project_root(__file__)  # repo root in the repo; this folder when deployed flat on the Pi
SETTINGS = ROOT / "settings.json"
RUNS = ROOT / "data" / "runs"
BUTTON_PIN = 17        # feedback button: physical pin 11 (GPIO17) to pin 9 (GND)
RETRAIN_EVERY = 20     # retrain after this many new feedback rows


def threshold():
    if not SETTINGS.exists():
        raise SystemExit("Missing settings.json. Run `python3 pi/canary.py calibrate` first.")
    return json.loads(SETTINGS.read_text())["threshold_dbm"]


def new_scanner(callback):
    from bleak import BleakScanner

    # BlueZ's DuplicateData filter helps, but callbacks are NOT a raw packet feed.
    return BleakScanner(callback, scanning_mode="active", bluez={"filters": {"DuplicateData": True}})


class Session:
    def __init__(self, demo_only=True):
        self.demo_only = demo_only
        self.trackers = {}
        self.signatures = {}
        self.count = 0
        self.on_sample = None

    def callback(self, device, advertisement):
        found = signature(advertisement, self.demo_only)
        if found is None:
            return
        now = time.monotonic()
        address = device.address
        self.trackers.setdefault(address, Tracker()).add(now, advertisement.rssi)
        self.signatures[address] = found
        self.count += 1
        if self.on_sample:
            self.on_sample(now, advertisement.rssi)

    def snapshots(self, now, limit):
        for address, tracker in list(self.trackers.items()):
            if tracker.last_seen is None or now - tracker.last_seen > 10:
                self.trackers.pop(address, None)
                self.signatures.pop(address, None)
                continue
            snap = tracker.snapshot(now, limit)
            if snap:
                yield address, snap


async def scan(seconds, demo_only):
    session = Session(demo_only)
    async with new_scanner(session.callback):
        previous = 0
        for elapsed in range(1, seconds + 1):
            await asyncio.sleep(1)
            fresh = session.count - previous
            previous = session.count
            candidates = list(session.trackers.values())
            newest = max((t.samples[-1].raw for t in candidates if t.samples), default=None)
            print(f"second={elapsed:02d} matching_callbacks={fresh} latest_rssi={newest}", flush=True)
    print(f"Total matching callbacks in {seconds} s: {session.count}")


async def calibrate():
    session = Session()
    measurements = []
    async with new_scanner(session.callback):
        for label, place in (("near", "2 metres"), ("far", "6 metres")):
            await asyncio.to_thread(
                input, f"Place advertising phone {place} from Pi; keep it still; press Enter here: "
            )
            readings = []
            session.on_sample = lambda _t, rssi: readings.append(rssi)
            print(f"Measuring {label} for 20 seconds...")
            await asyncio.sleep(20)
            session.on_sample = None
            if len(readings) < 5:
                raise SystemExit(f"Only {len(readings)} matching callbacks at {label}; check advertiser/scan setup.")
            median = statistics.median(readings)
            measurements.append(median)
            print(f"{label}: {len(readings)} callbacks, median RSSI {median:.1f} dBm")
    near, far = measurements
    if near - far < 6:
        raise SystemExit(
            f"Calibration gate failed: near/far differ by only {near - far:.1f} dB. "
            "Find a clear 6 m path and repeat before recording or demo."
        )
    chosen = round((near + far) / 2, 1)
    SETTINGS.write_text(json.dumps({"threshold_dbm": chosen, "near_median": near, "far_median": far}, indent=2) + "\n")
    print(f"Saved threshold {chosen} dBm to settings.json")


async def record(scenario):
    limit = threshold()
    session = Session()
    rows = []
    recording = False
    stopped_at = None
    leaving_at = None
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + scenario

    async def collect():
        while True:
            await asyncio.sleep(1)
            if not recording:
                continue
            now = time.monotonic()
            for _address, snap in session.snapshots(now, limit):
                label = int(
                    scenario == "linger"
                    and stopped_at is not None
                    and now >= stopped_at + 3
                    and leaving_at is None
                )
                rows.append({
                    "run_id": run_id,
                    "scenario": scenario,
                    "t": round(now, 3),
                    **{name: round(getattr(snap, name), 4) for name in MODEL_COLUMNS},
                    "close_streak_s": round(snap.close_streak_s, 4),
                    "packet_rate_hz": round(snap.packet_rate_hz, 4),
                    "label": label,
                })

    async with new_scanner(session.callback):
        task = asyncio.create_task(collect())
        try:
            print("Scanner warming up for 5 seconds; keep the phone at its run start point...")
            await asyncio.sleep(5)
            await asyncio.to_thread(input, "Press Enter to START this run: ")
            recording = True
            if scenario == "linger":
                await asyncio.to_thread(input, "Walk toward Pi now. Press Enter the moment you STOP: ")
                stopped_at = time.monotonic()
                await asyncio.to_thread(input, "Stay still 20 seconds. Press Enter when you START LEAVING: ")
                leaving_at = time.monotonic()
                await asyncio.to_thread(input, "Walk away. Press Enter when you finish this run: ")
            else:
                await asyncio.to_thread(input, "Perform the scenario; press Enter when finished: ")
            recording = False
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    if not rows:
        raise SystemExit("No evaluable rows. The phone may not be advertising or callbacks are too sparse.")
    if scenario == "linger" and not any(row["label"] for row in rows):
        raise SystemExit("No positive rows: hold the stop for at least 10 seconds and repeat.")
    RUNS.mkdir(parents=True, exist_ok=True)
    path = RUNS / f"{run_id}.csv"
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {len(rows)} rows ({sum(r['label'] for r in rows)} positive) to {path}")


class Motor:
    def __init__(self):
        from gpiozero import OutputDevice

        self.pin = OutputDevice(18, active_high=True, initial_value=False)

    async def pulse(self):
        try:
            self.pin.on()
            await asyncio.sleep(0.25)
            self.pin.off()
            await asyncio.sleep(0.15)
            self.pin.on()
            await asyncio.sleep(0.25)
        finally:
            self.pin.off()

    def close(self):
        self.pin.off()
        self.pin.close()


async def motor_test():
    motor = Motor()
    try:
        await motor.pulse()
        print("Sent two 250 ms pulses to GPIO18.")
    finally:
        motor.close()


class ModelFile:
    """model.joblib, reloaded automatically when retraining replaces it."""

    def __init__(self, path):
        self.path, self.mtime, self.model = path, None, None
        self.refresh()

    def refresh(self):
        if not self.path.exists():
            return False
        mtime = self.path.stat().st_mtime
        if mtime == self.mtime:
            return False
        import joblib

        self.model = joblib.load(self.path)  # Load only the file created by this team's train_model.py.
        self.mtime = mtime
        return True


def start_presses(use_button):
    """Feedback presses arrive on a queue: from the GPIO17 button, and from typing y + Enter."""
    import queue
    import sys
    import threading

    presses = queue.SimpleQueue()
    button = None
    if use_button:
        try:
            from gpiozero import Button

            button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.1)   # button between GPIO17 and GND
            button.when_pressed = lambda: presses.put("button")
        except Exception as error:   # no GPIO here (laptop) or pin busy: keyboard still works
            print(f"Feedback button unavailable ({error}); type y + Enter instead.")

    def keyboard():
        for line in sys.stdin:
            if line.strip().lower() == "y":
                presses.put("keyboard")

    threading.Thread(target=keyboard, daemon=True).start()
    return presses, button


def retrain_in_background(feedback_path):
    """Retrain with the latest feedback; live() picks up the new model.joblib when it lands."""
    import subprocess
    import sys

    args = [sys.executable, str(Path(__file__).resolve().parent / "train_model.py"), "--feedback", str(feedback_path)]
    sim_rows = next((p for p in (ROOT / "sim_rows.csv", ROOT / "data" / "sim_rows.csv") if p.exists()), None)
    if sim_rows:   # flat on the Pi it's next to the scripts; in the repo it's data/sim_rows.csv
        args += ["--sim-rows", str(sim_rows)]
    log = open(ROOT / "retrain.log", "a")
    return subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)


async def live(with_motor, use_model=False, feedback_on=True, retrain_every=RETRAIN_EVERY):
    from feedback import Feedback, FeedbackStore

    limit = threshold()
    session = Session()
    motor = Motor() if with_motor else None
    last_alert = {}
    model_file = ModelFile(ROOT / "model.joblib")
    if use_model and model_file.model is None:
        print("No model.joblib yet: the motor follows the 3-second rule until a model is trained.")
    model_alerts = {}
    feedback = Feedback(FeedbackStore(ROOT / "feedback.db")) if feedback_on else None
    presses, button = start_presses(with_motor) if feedback_on else (None, None)
    rows_since_train, retrain = 0, None
    driver = "the model (score >= 0.8 twice within 2.5 s)" if use_model else "the 3-second rule; ML score is display-only"
    print(f"Side Eye live: simulator packet only; threshold {limit} dBm; motor follows {driver}.")
    if feedback:
        print(f"Feedback on: press the button (GPIO{BUTTON_PIN}) or type y + Enter when someone is lingering. "
              f"{feedback.store.count()} feedback rows so far.")
    try:
        async with new_scanner(session.callback):
            while True:
                await asyncio.sleep(1)
                now = time.monotonic()
                if model_file.refresh() and model_file.mtime:
                    print("Model updated: now using the newly trained model.joblib.", flush=True)
                model = model_file.model
                active = list(session.snapshots(now, limit))
                if feedback:
                    while not presses.empty():
                        source = presses.get()
                        added = feedback.press(now)
                        rows_since_train += added
                        print(f"Feedback ({source}): {added} rows saved as 'lingering'.", flush=True)
                    rows_since_train += feedback.tick(now)
                    if rows_since_train >= retrain_every and (retrain is None or retrain.poll() is not None):
                        print(f"{rows_since_train} new feedback rows: retraining in the background.", flush=True)
                        retrain, rows_since_train = retrain_in_background(ROOT / "feedback.db"), 0
                if not active:
                    print("Waiting for simulator packet...", flush=True)
                    continue
                for address, snap in active:
                    probability = None
                    if model is not None:
                        probability = float(model.predict_proba([snap.model_vector()])[0][1])
                    if feedback:
                        feedback.observe(now, address, snap.model_vector())
                    if use_model and model is not None:
                        eligible = model_alerts.setdefault(address, ModelAlert()).update(now, probability)
                    else:
                        eligible = snap.close_streak_s >= 3.0
                    fire = eligible and now - last_alert.get(address, -1e9) >= 15
                    if fire:
                        last_alert[address] = now
                        if feedback:
                            feedback.alert(now, address)
                    score = f"{probability:.2f}" if probability is not None else "untrained"
                    print(
                        f"RSSI {snap.latest_rssi:4d} dBm | near streak {snap.close_streak_s:4.1f} s "
                        f"| ML linger score {score} | {'ALERT' if fire else 'watching'}",
                        flush=True,
                    )
                    if fire and motor:
                        await motor.pulse()
    finally:
        if motor:
            motor.close()
        if button:
            button.close()


def main():
    parser = argparse.ArgumentParser(description="Side Eye Pi 4 demo")
    commands = parser.add_subparsers(dest="command", required=True)
    scan_parser = commands.add_parser("scan", help="count matching BLE callbacks")
    scan_parser.add_argument("--seconds", type=int, default=20)
    scan_parser.add_argument("--real", action="store_true", help="scan broad company IDs; never use for staged demo")
    commands.add_parser("calibrate", help="measure the demo space and save threshold")
    record_parser = commands.add_parser("record", help="record one labeled run")
    record_parser.add_argument("scenario", choices=("passer", "linger", "far", "bag", "blocked"))
    commands.add_parser("motor-test", help="pulse the module connected to GPIO18")
    live_parser = commands.add_parser("live", help="run the haptic demo")
    live_parser.add_argument("--no-motor", action="store_true", help="terminal-only software check")
    live_parser.add_argument("--use-model", action="store_true",
                             help="motor follows the trained model (model.joblib) instead of the 3-second rule")
    live_parser.add_argument("--no-feedback", action="store_true",
                             help="don't record feedback presses or retrain")
    args = parser.parse_args()
    if args.command == "scan":
        asyncio.run(scan(args.seconds, not args.real))
    elif args.command == "calibrate":
        asyncio.run(calibrate())
    elif args.command == "record":
        asyncio.run(record(args.scenario))
    elif args.command == "motor-test":
        asyncio.run(motor_test())
    elif args.command == "live":
        asyncio.run(live(not args.no_motor, args.use_model, not args.no_feedback))


if __name__ == "__main__":
    main()
