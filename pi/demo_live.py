"""Demo mode (docs/DEMO.md): buzz fast and insistently when the transmitter is almost touching.

Usage (on the Pi, in the folder with Lili's canary.py):
  python3 demo_live.py              # with the motor
  python3 demo_live.py --no-motor   # screen only

Close contact shows up as very strong readings (-1 to -24 dBm in the demo room) that never happen
at ~1 m (strongest seen there: -27). So instead of "smoothed signal above a line for 3 s", demo mode
buzzes when at least COUNT readings stronger than STRONG arrived in the last WINDOW seconds, and
keeps buzzing every COOLDOWN seconds while that stays true. Reuses Lili's canary.py for scanning
and the motor pin (GPIO18).
"""
import argparse
import asyncio
import time

from canary import Motor, Session, new_scanner


async def pulse(motor, pulses, on_s, off_s):
    try:
        for _ in range(pulses):
            motor.pin.on()
            await asyncio.sleep(on_s)
            motor.pin.off()
            await asyncio.sleep(off_s)
    finally:
        motor.pin.off()


def strong_count(tracker, now, strong, window):
    return sum(1 for s in tracker.samples if s.t >= now - window and s.raw >= strong)


async def main(args):
    session = Session()
    motor = None if args.no_motor else Motor()
    last_buzz = -1e9
    print(f"Side Eye demo mode: buzz when >= {args.count} readings stronger than {args.strong} dBm "
          f"arrive within {args.window:g} s; repeat every {args.cooldown:g} s while close.", flush=True)
    try:
        async with new_scanner(session.callback):
            while True:
                await asyncio.sleep(0.5)
                now = time.monotonic()
                live = [(a, t) for a, t in session.trackers.items() if t.samples and now - t.samples[-1].t <= 2]
                if not live:
                    print("Waiting for transmitter...", flush=True)
                    continue
                address, tracker = max(live, key=lambda item: strong_count(item[1], now, args.strong, args.window))
                n = strong_count(tracker, now, args.strong, args.window)
                peak = max(s.raw for s in tracker.samples if s.t >= now - args.window)
                close = n >= args.count
                fire = close and now - last_buzz >= args.cooldown
                print(f"strongest {peak:4d} dBm | strong readings {n:2d} | {'BUZZ' if fire else ('close' if close else 'watching')}",
                      flush=True)
                if fire:
                    last_buzz = now
                    if motor:
                        await pulse(motor, args.pulses, args.on, args.off)
    finally:
        if motor:
            motor.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--strong", type=int, default=-24, help="a reading this strong or stronger counts (dBm)")
    ap.add_argument("--count", type=int, default=3, help="strong readings needed within --window")
    ap.add_argument("--window", type=float, default=2.0, help="seconds")
    ap.add_argument("--cooldown", type=float, default=3.0, help="seconds between buzzes while still close")
    ap.add_argument("--pulses", type=int, default=3)
    ap.add_argument("--on", type=float, default=0.4, help="seconds per pulse")
    ap.add_argument("--off", type=float, default=0.15, help="seconds between pulses")
    ap.add_argument("--no-motor", action="store_true")
    try:
        asyncio.run(main(ap.parse_args()))
    except KeyboardInterrupt:
        print("\nStopped.")
