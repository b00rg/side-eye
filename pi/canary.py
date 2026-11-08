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
BUTTON_PIN = 17        # wire button: physical pin 11 (GPIO17) to pin 9 (GND), see WireButton


def threshold():
    if not SETTINGS.exists():
        raise SystemExit("Missing settings.json. Run `python3 pi/canary.py calibrate` first.")
    return json.loads(SETTINGS.read_text())["threshold_dbm"]


SCANNER = "bleak"   # "hci" with `canary.py --hci ...`: raw HCI, every advert (see hci_scan.py)


def new_scanner(callback):
    if SCANNER == "hci":
        from hci_scan import HciScanner

        return HciScanner(callback)
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
    async with new_scanner(session.callback) as scanner:
        previous, previous_all = 0, 0
        for elapsed in range(1, seconds + 1):
            await asyncio.sleep(1)
            fresh = session.count - previous
            previous = session.count
            candidates = list(session.trackers.values())
            newest = max((t.samples[-1].raw for t in candidates if t.samples), default=None)
            line = f"second={elapsed:02d} matching_callbacks={fresh} latest_rssi={newest}"
            total = getattr(scanner, "total_adverts", None)   # --hci only: adverts from every device
            if total is not None:
                line += f" all_devices={total - previous_all}"
                previous_all = total
            print(line, flush=True)
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

    async def tick(self, count):
        """Short confirmation ticks (60 ms), clearly different from the 250 ms alarm."""
        try:
            for i in range(count):
                self.pin.on()
                await asyncio.sleep(0.06)
                self.pin.off()
                if i < count - 1:
                    await asyncio.sleep(0.15)
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

        self.model = joblib.load(self.path)  # Load only files created by this team's own training scripts.
        self.mtime = mtime
        return True


class WireButton:
    """Two jumper wires: one on GPIO17 (pin 11), one on GND (pin 9). Touch the ends = press.

    The Pi's internal pull-up holds GPIO17 high; touching it to ground pulls it low.
    Never touch the GPIO wire to a 3.3 V or 5 V pin.
    """

    def __init__(self, pin=BUTTON_PIN):
        from collections import deque
        from gpiozero import Button

        self.button = Button(pin, pull_up=True, bounce_time=0.05)
        self.durations = deque()
        self._down_at = None
        self.button.when_pressed = self._pressed
        self.button.when_released = self._released

    def _pressed(self):
        self._down_at = time.monotonic()

    def _released(self):
        if self._down_at is not None:
            self.durations.append(time.monotonic() - self._down_at)
            self._down_at = None

    def presses(self):
        while self.durations:
            yield self.durations.popleft()

    def close(self):
        self.button.close()


async def button_test(seconds):
    from canary_core import HOLD_S

    button = WireButton()
    print(f"Touch the GPIO17 and GND wire ends together for {seconds} s. Hold >= {HOLD_S} s counts as a hold.")
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            await asyncio.sleep(0.2)
            for duration in button.presses():
                print(f"{'HOLD' if duration >= HOLD_S else 'tap '}  {duration:.2f} s", flush=True)
    finally:
        button.close()


def open_store_and_button(with_db, with_button):
    """Tiger Data upload (if credentials are set up) and the GPIO17 wire button (only useful with uploads)."""
    store = glasses_ref = button = None
    if with_db:
        from alarm_store import AlarmStore, GlassesRef

        store = AlarmStore.from_env()
        glasses_ref = GlassesRef()
    if store and with_button:
        try:
            button = WireButton()
            print("Wire button on GPIO17: touch after a buzz = glasses there, hold 2 s = false alarm, "
                  "touch any other time = missed glasses.")
        except Exception as error:  # no gpiozero / not a Pi
            print(f"Wire button off ({type(error).__name__}).")
    return store, glasses_ref, button


async def handle_presses(button, store, last_buzz, now, report_missed, motor=None):
    """Turn wire-button presses into Tiger labels; returns the updated last_buzz.

    last_buzz is (uploaded row, monotonic time) for the latest buzz, or None. report_missed() records a
    missed-glasses row and returns True if signal features were attached.
    """
    from canary_core import classify_press

    for duration in button.presses():
        since = now - last_buzz[1] if last_buzz else None
        kind = classify_press(duration, since)
        if kind in ("tp", "fp"):
            store.label(last_buzz[0], kind == "tp")
            last_buzz = None
            print("Labelled last buzz: " + ("TP, glasses were there" if kind == "tp" else "FP, false alarm"), flush=True)
        else:
            with_features = report_missed()
            print("Reported missed glasses (FN)" + (" with signal features" if with_features else ""), flush=True)
        if motor:
            await motor.tick({"tp": 1, "fp": 2, "fn": 3}[kind])
    return last_buzz


async def live(with_motor, with_db=True, with_button=True, use_model=False):
    limit = threshold()
    session = Session()
    motor = Motor() if with_motor else None
    store, glasses_ref, button = open_store_and_button(with_db, with_button)
    if store:
        from alarm_store import make_alarm, make_missed
    last_alert = {}
    last_buzz = None  # (row uploaded for the latest buzz, monotonic time), cleared once labelled
    model_file = ModelFile(ROOT / "model.joblib")   # reloaded when retraining replaces it
    model_alerts = {}
    if use_model and model_file.model is None:
        print("No model.joblib yet: the motor follows the 3-second rule until a model is trained.")

    def score(snap):
        model = model_file.model
        return float(model.predict_proba([snap.model_vector()])[0][1]) if model is not None else None

    driver = "the level model (score >= 0.8 twice within 2.5 s)" if use_model else \
        "the 3-second rule; ML score is display-only"
    print(f"Canary live: simulator packet only; threshold {limit} dBm; motor follows {driver}.")
    try:
        async with new_scanner(session.callback):
            while True:
                await asyncio.sleep(1)
                now = time.monotonic()
                if model_file.refresh():
                    print("Model updated: now using the newly trained model.joblib.", flush=True)
                active = list(session.snapshots(now, limit))
                if button:
                    def report_missed():
                        # Attach the strongest device the Pi is tracking right now, if any, so the miss is trainable.
                        address, snap = max(active, key=lambda item: item[1].mean_rssi, default=(None, None))
                        store.record(make_missed(
                            glasses_ref, address, session.signatures.get(address) if address else None,
                            snap, score(snap) if snap else None))
                        return snap is not None

                    last_buzz = await handle_presses(button, store, last_buzz, now, report_missed, motor)
                if not active:
                    print("Waiting for simulator packet...", flush=True)
                    continue
                for address, snap in active:
                    probability = score(snap)
                    if use_model and probability is not None:
                        eligible = model_alerts.setdefault(address, ModelAlert()).update(now, probability)
                    else:
                        eligible = snap.close_streak_s >= 3.0
                    fire = eligible and now - last_alert.get(address, -1e9) >= 15
                    if fire:
                        last_alert[address] = now
                        if store:
                            # Only buzzes are uploaded; the address is hashed inside make_alarm.
                            alarm = make_alarm(address, session.signatures.get(address), snap, probability, glasses_ref,
                                               rule="level-model" if use_model and probability is not None else "3s-streak")
                            store.record(alarm)
                            last_buzz = (alarm, now)
                    shown = f"{probability:.2f}" if probability is not None else "untrained"
                    print(
                        f"RSSI {snap.latest_rssi:4d} dBm | avg {snap.mean_rssi:6.1f} dBm | near streak {snap.close_streak_s:4.1f} s "
                        f"| ML linger score {shown} | {'ALERT' if fire else 'watching'}",
                        flush=True,
                    )
                    if fire and motor:
                        await motor.pulse()
    finally:
        if motor:
            motor.close()
        if button:
            button.close()
        if store:
            store.close()


def main():
    parser = argparse.ArgumentParser(description="Side Eye Pi 4 demo")
    parser.add_argument("--hci", action="store_true",
                        help="scan via raw HCI: every advert, not BlueZ's thinned updates (needs sudo or setcap)")
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
                             help="motor follows the level model (model.joblib) instead of the 3-second rule "
                                  "(the shape model runs in shape_live.py)")
    live_parser.add_argument("--no-db", action="store_true", help="don't upload alarms to Tiger Data")
    live_parser.add_argument("--no-button", action="store_true", help="ignore the wire button on GPIO17")
    button_parser = commands.add_parser("button-test", help="print taps/holds from the GPIO17 wire button")
    button_parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    global SCANNER
    if args.hci:
        SCANNER = "hci"
    if args.command == "scan":
        asyncio.run(scan(args.seconds, not args.real))
    elif args.command == "calibrate":
        asyncio.run(calibrate())
    elif args.command == "record":
        asyncio.run(record(args.scenario))
    elif args.command == "motor-test":
        asyncio.run(motor_test())
    elif args.command == "live":
        asyncio.run(live(not args.no_motor, not args.no_db, not args.no_button, args.use_model))
    elif args.command == "button-test":
        asyncio.run(button_test(args.seconds))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
