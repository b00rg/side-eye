"""Put clearly-marked dummy labelled alarms in Tiger Data, to test retraining end to end.

    python3 scripts/dummy_events.py add [--n 60]   # insert dummy TP / FP / FN events
    python3 scripts/dummy_events.py count          # how many dummy events are there
    python3 scripts/dummy_events.py remove         # delete every dummy event

Every row has device_id = 'dummy-data' and a note saying so; `remove` deletes exactly those.
They are not real measurements: annotate.py's report counts them until they are removed.
Feature values are invented to look like the walks: glasses there = strong, steady, long dwell;
false alarms = near the cutoff, noisier, shorter dwell.
"""

import argparse
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pi"))

from alarm_store import COLUMNS, INSERT_SQL, service_url  # noqa: E402

DEVICE = "dummy-data"
NOTE = "dummy data for testing retraining; remove with scripts/dummy_events.py remove"
# Share of each outcome, roughly what a noisy 3 s rule might produce.
OUTCOMES = (("TP", 0.5), ("FP", 0.35), ("FN", 0.15))


def dummy_rows(n, seed=7, now=None):
    rng = random.Random(seed)
    shape_rng = random.Random(seed + 1)   # separate stream: the level features stay as they were
    now = now or datetime.now(timezone.utc)
    rows = []
    for i in range(n):
        outcome = rng.choices([o for o, _ in OUTCOMES], [w for _, w in OUTCOMES])[0]
        present = outcome in ("TP", "FN")
        if outcome == "TP":
            mean, var, slope, dwell = rng.gauss(-58, 4), rng.uniform(0.5, 4), rng.gauss(0, 0.4), rng.uniform(6, 10)
        elif outcome == "FP":
            mean, var, slope, dwell = rng.gauss(-67, 3), rng.uniform(4, 14), rng.gauss(1.2, 0.8), rng.uniform(3, 5)
        else:  # FN: glasses there but the rule stayed quiet
            mean, var, slope, dwell = rng.gauss(-62, 4), rng.uniform(1, 6), rng.gauss(0, 0.6), rng.uniform(2, 6)
        # shape inputs (rise, drop, steady, held) as shape_live.py would upload them
        u = shape_rng.uniform
        rise, drop, steady, held = {"TP": (u(6, 14), u(0, 1), u(0.5, 2), u(4, 12)),     # arrived and stayed
                                    "FP": (u(5, 10), u(0, 2), u(2, 4), u(1, 4)),       # peak of a walk-past
                                    "FN": (u(2, 6), u(0, 3), u(1, 3), u(0, 3))}[outcome]
        buzzed = outcome != "FN"
        time = now - timedelta(hours=rng.uniform(1, 72))
        row = dict.fromkeys(COLUMNS)
        row.update(
            event_id=uuid.uuid4(), time=time, device_id=DEVICE, buzzed=buzzed,
            glasses_present=present, annotated_at=time + timedelta(minutes=2),
            source="pi" if buzzed else "user", note=NOTE,
            glasses_ref="dummy" if buzzed else None, signature="phone_simulator", demo=True,
            rule="3s-streak" if buzzed else None,
            rssi=round(mean + rng.gauss(0, 2)), mean_rssi=round(mean, 1), var_rssi=round(var, 2),
            slope=round(slope, 2), dwell_s=round(dwell, 1), close_streak_s=round(min(dwell, 3 + rng.uniform(0, 4)), 1),
            rise=round(rise, 2), drop=round(drop, 2), steady=round(steady, 2), held=float(round(held)),
        )
        rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("add", "count", "remove"))
    parser.add_argument("--n", type=int, default=60, help="events to add (default 60)")
    args = parser.parse_args(argv)
    url = service_url()
    if not url:
        raise SystemExit("No TIMESCALE_SERVICE_URL found (.env, $SIDE_EYE_ENV, or the environment).")
    import psycopg

    with psycopg.connect(url, connect_timeout=10) as conn:
        if args.action == "add":
            rows = dummy_rows(args.n)
            with conn.cursor() as cursor:
                cursor.executemany(INSERT_SQL, [tuple(row[c] for c in COLUMNS) for row in rows])
            print(f"Added {len(rows)} dummy events (device_id = '{DEVICE}').")
        elif args.action == "remove":
            deleted = conn.execute("DELETE FROM events WHERE device_id = %s", (DEVICE,)).rowcount
            print(f"Removed {deleted} dummy events.")
        summary = conn.execute(
            "SELECT outcome, count(*) FROM event_outcomes WHERE device_id = %s GROUP BY 1 ORDER BY 1",
            (DEVICE,)).fetchall()
        print("Dummy events now:", ", ".join(f"{o} {c}" for o, c in summary) or "none")


if __name__ == "__main__":
    main()
