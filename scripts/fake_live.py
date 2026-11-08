"""Run the Pi's real shape_live.py loop on a laptop, with a fake radio, wire button and Tiger store.

Usage (from the repo root):
  python3 scripts/fake_live.py                                   # summary of buzzes, labels, uploads
  python3 scripts/fake_live.py --stream | python3 scripts/live_graph.py --stdin   # watch it as a graph

Builds a throwaway "Pi folder" (code + settings.json), trains the shape model there on session 1
(pi/train_shape.py, as on the Pi), then runs the unmodified shape_live main() with only these swapped:
- the Bluetooth scanner replays recorded session-2 walks (each second's smoothed signal, sent as five
  adverts a second), with quiet gaps between walks;
- the clock runs fast;
- the wire button: the "wearer" touches it 3 s after each buzz on a linger (glasses there) and holds
  it after a buzz on any other walk (false alarm);
- Tiger Data: rows are kept in memory and summarised at the end (nothing leaves the laptop).

A pipeline test, not a measurement: walks run back to back, so each walk's shape history carries over
into the next, and the replayed adverts are each second's already-smoothed value. For results use
nist/shape_confirm.py.
"""
import argparse
import asyncio
import builtins
import contextlib
import csv
import os
import shutil
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
GAP_S = 20                  # quiet seconds between walks
PRESS_AFTER_BUZZ_S = 3
TEST = REPO / "data" / "runs_session2"


def make_pi_folder(root):
    for name in ("canary.py", "canary_core.py", "train_model.py", "shape_core.py", "shape_live.py",
                 "train_shape.py", "alarm_store.py"):
        shutil.copy(REPO / "pi" / name, root / name)
    shutil.copy(REPO / "settings.json", root / "settings.json")
    out = subprocess.run([sys.executable, "-W", "ignore", "train_shape.py", str(REPO / "data" / "runs_session1")],
                         cwd=root, capture_output=True, text=True, check=True).stdout
    print("Pi folder ready:", out.strip().splitlines()[-1])


def session_readings():
    """Session-2 walks back to back: (time, rssi) adverts and (start, end, kind) per walk."""
    readings, walks, t0 = [], [], 0.0
    for path in sorted(TEST.glob("*.csv")):
        with path.open(newline="") as f:
            rows = sorted(csv.DictReader(f), key=lambda r: float(r["t"]))
        start = float(rows[0]["t"])
        for r in rows:
            t = t0 + float(r["t"]) - start
            readings += [(t + k * 0.2, round(float(r["mean_rssi"]))) for k in range(5)]
        length = float(rows[-1]["t"]) - start + 1
        walks.append((t0, t0 + length, rows[0]["scenario"]))
        t0 += length + GAP_S
    return readings, walks, t0


class FakeStore:
    def __init__(self):
        self.rows, self.labels = [], []

    def record(self, row):
        self.rows.append(row)

    def label(self, row, glasses_present):
        self.labels.append((row, glasses_present))

    def close(self):
        pass


class FakeButton:
    def __init__(self):
        self.durations = deque()

    def presses(self):
        while self.durations:
            yield self.durations.popleft()

    def close(self):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", action="store_true",
                    help="print shape_live's output unfiltered, paced in real time (pipe into live_graph.py --stdin)")
    ap.add_argument("--speed", type=float, default=1.0, help="with --stream: 2 = twice real time")
    args = ap.parse_args()
    root = Path(tempfile.mkdtemp(prefix="side-eye-pi-"))
    with contextlib.redirect_stdout(open(os.devnull, "w")) if args.stream else contextlib.nullcontext():
        make_pi_folder(root)
    sys.path.insert(0, str(root))
    os.chdir(root)
    import alarm_store  # noqa: E402  (the Pi folder's copies)
    import canary  # noqa: E402
    import shape_live  # noqa: E402

    readings, walks, end = session_readings()
    clock = {"t": 0.0}
    state = {"callback": None, "i": 0, "buzzes": 0}
    store, button = FakeStore(), FakeButton()
    pending = []   # (time, touch duration)

    def walk_at(t):
        return next((kind for start, stop, kind in walks if start <= t <= stop + 5), None)

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
        while pending and pending[0][0] <= target:
            button.durations.append(pending.pop(0)[1])
        if clock["t"] > end + 10:
            raise KeyboardInterrupt
        await real_sleep(seconds / args.speed if args.stream else 0)

    async def fake_buzz(motor, pulses, on_s, off_s):
        pass

    real_record = store.record

    def record(row):   # the wearer reacts to each buzz
        real_record(row)
        if row["buzzed"]:
            kind = walk_at(clock["t"])
            pending.append((clock["t"] + PRESS_AFTER_BUZZ_S, 0.2 if kind == "linger" else 2.0))
    store.record = record

    shape_live.new_scanner = FakeScanner
    shape_live.time = canary.time = SimpleNamespace(monotonic=lambda: clock["t"])
    shape_live.asyncio = SimpleNamespace(sleep=fast_sleep)
    shape_live.buzz = fake_buzz
    canary.WireButton = lambda: button
    alarm_store.AlarmStore.from_env = staticmethod(lambda: store)
    run_args = SimpleNamespace(no_motor=True, no_db=False, no_button=False, alert=0.8, cooldown=15.0,
                               pulses=3, on=0.4, off=0.15)

    if args.stream:
        try:
            asyncio.run(shape_live.main(run_args))
        except (KeyboardInterrupt, BrokenPipeError):
            # The graph window was closed: point stdout at /dev/null so the exit flush stays quiet.
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return

    print(f"Replaying {len(walks)} session-2 walks ({end / 60:.1f} min of fake time)\n")
    interesting = ("ALERT", "Labelled", "Reported", "Side Eye live", "Wire button", "Model updated")
    real_print = print

    def quiet_print(*a, **kw):
        text = " ".join(str(x) for x in a)
        if any(k in text for k in interesting):
            kind = walk_at(clock["t"]) or "gap"
            real_print(f"[{clock['t']:6.0f}s {kind:>7}] {text.split(' | rise')[0]}",
                       **{k: v for k, v in kw.items() if k != "flush"})
    builtins.print = quiet_print
    try:
        asyncio.run(shape_live.main(run_args))
    except KeyboardInterrupt:
        pass
    finally:
        builtins.print = real_print

    buzzes = [r for r in store.rows if r["buzzed"]]
    tp = sum(1 for _, present in store.labels if present)
    print(f"\nWould upload to Tiger: {len(buzzes)} buzzes (rule {buzzes[0]['rule'] if buzzes else '-'}), "
          f"labelled {tp} glasses there / {len(store.labels) - tp} false alarm, "
          f"every row with shape inputs: {all(r['rise'] is not None for r in store.rows)}")
    print(f"Pi folder left at {root} for inspection.")


if __name__ == "__main__":
    main()
