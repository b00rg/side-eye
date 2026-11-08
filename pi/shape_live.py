"""Live detector driven by the shape model (docs/RESULTS.md section 2).

Usage (on the Pi, in the folder with Lili's canary.py, after train_shape.py):
  python3 shape_live.py               # with the motor
  python3 shape_live.py --no-motor    # screen only

Every second, for the transmitter: the Pi's usual smoothed snapshot (canary_core.Tracker, same as
recording) -> shape inputs (shape_core.ShapeTracker) -> shape model score. Buzzes when the score is
>= 0.8 twice within 2.5 s (the alert rule used in every test), then waits COOLDOWN seconds. Lines
are printed in the same format as `canary.py live`, so scripts/live_graph.py can draw them.
"""
import argparse
import asyncio
import time

import joblib

from canary import Motor, Session, new_scanner, threshold
from canary_core import ModelAlert, project_root
from shape_core import ShapeTracker


async def buzz(motor, pulses, on_s, off_s):
    try:
        for _ in range(pulses):
            motor.pin.on()
            await asyncio.sleep(on_s)
            motor.pin.off()
            await asyncio.sleep(off_s)
    finally:
        motor.pin.off()


async def main(args):
    model = joblib.load(project_root(__file__) / "shape_model.joblib")
    limit = threshold()
    session = Session()
    motor = None if args.no_motor else Motor()
    shapes, alerts, last_buzz = {}, {}, {}
    print(f"Side Eye live: shape model; threshold {limit} dBm (display only); buzz when score >= {args.alert:g} twice "
          f"within 2.5 s, then wait {args.cooldown:g} s.", flush=True)
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
                    features = shapes.setdefault(address, ShapeTracker()).update(snap.mean_rssi)
                    score = float(model.predict_proba([features])[0][1])
                    fire = alerts.setdefault(address, ModelAlert(threshold=args.alert)).update(now, score) \
                        and now - last_buzz.get(address, -1e9) >= args.cooldown
                    if fire:
                        last_buzz[address] = now
                    rise, drop, steady, held = features
                    print(f"RSSI {snap.latest_rssi:4d} dBm | avg {snap.mean_rssi:6.1f} dBm | near streak "
                          f"{snap.close_streak_s:4.1f} s | ML linger score {score:.2f} | {'ALERT' if fire else 'watching'}"
                          f" | rise {rise:4.1f} drop {drop:4.1f} steady {steady:4.1f} held {held:3.0f}", flush=True)
                    if fire and motor:
                        await buzz(motor, args.pulses, args.on, args.off)
    finally:
        if motor:
            motor.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-motor", action="store_true")
    ap.add_argument("--alert", type=float, default=0.8,
                    help="score needed twice within 2.5 s (0.8 = the tested setting; lower = more eager, e.g. 0.7 for the demo)")
    ap.add_argument("--cooldown", type=float, default=15.0, help="seconds between buzzes (default 15, as tested)")
    ap.add_argument("--pulses", type=int, default=3)
    ap.add_argument("--on", type=float, default=0.4)
    ap.add_argument("--off", type=float, default=0.15)
    try:
        asyncio.run(main(ap.parse_args()))
    except KeyboardInterrupt:
        print("\nStopped.")
