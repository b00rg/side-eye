"""Live detector driven by the shape model (docs/RESULTS.md section 2).

Usage (on the Pi, after train_shape.py or retrain_shape.py has written shape_model.joblib):
  python3 shape_live.py               # with the motor, Tiger uploads and the wire button if set up
  python3 shape_live.py --no-motor    # screen only
  python3 shape_live.py --no-db       # no Tiger uploads (and so no button labels)

Every second, for the transmitter: the Pi's usual smoothed snapshot (canary_core.Tracker, same as
recording) -> shape inputs (shape_core.ShapeTracker) -> shape model score. Buzzes when the score is
>= 0.8 twice within 2.5 s (the alert rule used in every test), then waits COOLDOWN seconds. Lines
are printed in the same format as `canary.py live`, so scripts/live_graph.py can draw them.

Labelling (same as `canary.py live`, Emma's wire button on GPIO17): touch after a buzz = glasses
were there (TP), hold = false alarm (FP), touch any other time = missed glasses (FN). Each buzz and
report goes to Tiger Data with the four shape inputs at that second, which is what
retrain_shape.py learns from. shape_model.joblib is reloaded when a retrain replaces it.
"""
import argparse
import asyncio
import time

from canary import Motor, Session, new_scanner, threshold
from canary_core import ModelAlert, project_root
from shape_core import ShapeTracker

try:   # newer canary.py: Tiger uploads, wire button, model reload
    from canary import ModelFile, handle_presses, open_store_and_button
except ImportError:   # an older canary.py on the Pi: run exactly as before (no uploads, no button)
    ModelFile = handle_presses = open_store_and_button = None

MODEL_NAME = "shape_model.joblib"


class _StaticModel:
    """Fallback for an older canary.py without ModelFile: load once."""

    def __init__(self, path):
        import joblib

        self.model = joblib.load(path)

    def refresh(self):
        return False


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
    path = project_root(__file__) / MODEL_NAME
    if not path.exists():
        raise SystemExit(f"No {MODEL_NAME}: run train_shape.py (or retrain_shape.py) first.")
    model_file = ModelFile(path) if ModelFile else _StaticModel(path)
    limit = threshold()
    session = Session()
    motor = None if args.no_motor else Motor()
    store = glasses_ref = button = None
    if open_store_and_button:
        store, glasses_ref, button = open_store_and_button(not args.no_db, not args.no_button)
    else:
        print("Tiger uploads and wire button off: this canary.py predates them.")
    if store:
        from alarm_store import make_alarm, make_missed
    shapes, alerts, last_alert, latest = {}, {}, {}, {}
    last_buzz = None   # (uploaded row, monotonic time) for the latest buzz, until it is labelled
    print(f"Side Eye live: shape model; threshold {limit} dBm (display only); buzz when score >= {args.alert:g} twice "
          f"within 2.5 s, then wait {args.cooldown:g} s.", flush=True)
    try:
        async with new_scanner(session.callback):
            while True:
                await asyncio.sleep(1)
                now = time.monotonic()
                if model_file.refresh():
                    print(f"Model updated: now using the newly trained {MODEL_NAME}.", flush=True)
                active = list(session.snapshots(now, limit))
                if button:
                    def report_missed():
                        # The strongest tracked device, with its shape inputs, so the miss is trainable.
                        address, snap = max(active, key=lambda item: item[1].mean_rssi, default=(None, None))
                        features, score = latest.get(address, (None, None))
                        store.record(make_missed(glasses_ref, address,
                                                 session.signatures.get(address) if address else None,
                                                 snap, score, shape=features))
                        return features is not None

                    last_buzz = await handle_presses(button, store, last_buzz, now, report_missed, motor)
                if not active:
                    print("Waiting for simulator packet...", flush=True)
                    continue
                for address, snap in active:
                    features = shapes.setdefault(address, ShapeTracker()).update(snap.mean_rssi)
                    score = float(model_file.model.predict_proba([features])[0][1])
                    latest[address] = (features, score)
                    fire = alerts.setdefault(address, ModelAlert(threshold=args.alert)).update(now, score) \
                        and now - last_alert.get(address, -1e9) >= args.cooldown
                    if fire:
                        last_alert[address] = now
                        if store:
                            # Only buzzes are uploaded; the address is hashed inside make_alarm.
                            alarm = make_alarm(address, session.signatures.get(address), snap, score, glasses_ref,
                                               rule="shape-model", shape=features)
                            store.record(alarm)
                            last_buzz = (alarm, now)
                    rise, drop, steady, held = features
                    print(f"RSSI {snap.latest_rssi:4d} dBm | avg {snap.mean_rssi:6.1f} dBm | near streak "
                          f"{snap.close_streak_s:4.1f} s | ML linger score {score:.2f} | {'ALERT' if fire else 'watching'}"
                          f" | rise {rise:4.1f} drop {drop:4.1f} steady {steady:4.1f} held {held:3.0f}", flush=True)
                    if fire and motor:
                        await buzz(motor, args.pulses, args.on, args.off)
    finally:
        if motor:
            motor.close()
        if button:
            button.close()
        if store:
            store.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-motor", action="store_true")
    ap.add_argument("--no-db", action="store_true", help="don't upload buzzes to Tiger Data")
    ap.add_argument("--no-button", action="store_true", help="ignore the wire button on GPIO17")
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
