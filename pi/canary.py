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


async def live(with_motor, use_model=False):
    limit = threshold()
    session = Session()
    motor = Motor() if with_motor else None
    last_alert = {}
    model = None
    model_path = ROOT / "model.joblib"
    if model_path.exists():
        import joblib

        model = joblib.load(model_path)  # Load only the file created by this team's train_model.py.
    if use_model and model is None:
        raise SystemExit("--use-model needs model.joblib. Train it first: python3 train_model.py --sim-rows sim_rows.csv")
    model_alerts = {}
    driver = "the model (score >= 0.8 twice within 2.5 s)" if use_model else "the 3-second rule; ML score is display-only"
    print(f"Side Eye live: simulator packet only; threshold {limit} dBm; motor follows {driver}.")
    try:
        async with new_scanner(session.callback):
            while True:
                await asyncio.sleep(1)
                now = time.monotonic()
                active = list(session.snapshots(now, limit))
                if not active:
                    print("Waiting for simulator packet...", flush=True)
                    continue
                for address, snap in active:
                    probability = None
                    if model is not None:
                        probability = float(model.predict_proba([snap.model_vector()])[0][1])
                    if use_model:
                        eligible = model_alerts.setdefault(address, ModelAlert()).update(now, probability)
                    else:
                        eligible = snap.close_streak_s >= 3.0
                    fire = eligible and now - last_alert.get(address, -1e9) >= 15
                    if fire:
                        last_alert[address] = now
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
        asyncio.run(live(not args.no_motor, args.use_model))


if __name__ == "__main__":
    main()
