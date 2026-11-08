"""Annotate Side Eye events on Tiger Data and see how the detector is doing.

    python3 scripts/annotate.py pending              # buzzes waiting for a label
    python3 scripts/annotate.py tp                   # latest pending buzz: glasses WERE there  -> TP
    python3 scripts/annotate.py fp                   # latest pending buzz: NO glasses          -> FP
    python3 scripts/annotate.py tp 3f9a1c2e          # label a specific buzz by id prefix
    python3 scripts/annotate.py missed               # glasses were there, no buzz (now)        -> FN
    python3 scripts/annotate.py missed --at "2026-11-07 14:03" --device pi-emma --note "on the bus"
    python3 scripts/annotate.py report               # TP / FP / FN, precision, recall, recent rows

No glasses and no buzz is a true negative: it is never stored (the database rejects it).
Add --real to `report` or `pending` to leave out demo (phone simulator) events.
"""

import argparse
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pi"))

import psycopg  # noqa: E402

from alarm_store import device_id, service_url  # noqa: E402


def find_pending(conn, prefix):
    if prefix:
        rows = conn.execute(
            "SELECT event_id, time FROM events WHERE buzzed AND event_id::text LIKE %s",
            (prefix.lower() + "%",)).fetchall()
        if not rows:
            raise SystemExit(f"No buzz with id starting {prefix!r}.")
        if len(rows) > 1:
            raise SystemExit(f"Id prefix {prefix!r} matches {len(rows)} buzzes; use more characters.")
        return rows[0]
    row = conn.execute(
        "SELECT event_id, time FROM events WHERE buzzed AND glasses_present IS NULL "
        "ORDER BY time DESC LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("No pending buzzes to label.")
    return row


def label(conn, prefix, present, note):
    event_id, time = find_pending(conn, prefix)
    conn.execute(
        "UPDATE events SET glasses_present = %s, annotated_at = now(), note = coalesce(%s, note) "
        "WHERE event_id = %s AND time = %s",
        (present, note, event_id, time))
    print(f"{str(event_id)[:8]} at {time:%H:%M:%S} -> {'TP (glasses present)' if present else 'FP (no glasses)'}")


def missed(conn, at, device, note):
    when = datetime.fromisoformat(at).astimezone() if at else datetime.now().astimezone()
    event_id = uuid.uuid4()
    conn.execute(
        "INSERT INTO events (event_id, time, device_id, buzzed, glasses_present, annotated_at, source, note) "
        "VALUES (%s, %s, %s, false, true, now(), 'user', %s)",
        (event_id, when, device, note))
    print(f"{str(event_id)[:8]} at {when:%Y-%m-%d %H:%M} on {device} -> FN (glasses present, no buzz)")


def pending(conn, real):
    rows = conn.execute(
        "SELECT event_id, time, device_id, signature, rssi, close_streak_s, ml_score FROM events "
        f"WHERE buzzed AND glasses_present IS NULL {'AND NOT demo' if real else ''} "
        "ORDER BY time DESC LIMIT 50").fetchall()
    if not rows:
        print("Nothing pending.")
        return
    print(f"{'id':8}  {'time':19}  {'device':14} {'signature':16} {'rssi':>5} {'streak':>7} {'ml':>5}")
    for event_id, time, dev, sig, rssi, streak, ml in rows:
        ml_text = "-" if ml is None else f"{ml:.2f}"
        print(f"{str(event_id)[:8]}  {time.astimezone():%Y-%m-%d %H:%M:%S}  {dev[:14]:14} {(sig or '-')[:16]:16} "
              f"{rssi or 0:5d} {streak or 0:6.1f}s {ml_text:>5}")


def report(conn, real):
    where = "WHERE NOT demo" if real else ""
    tp, fp, fn, waiting = conn.execute(
        "SELECT count(*) FILTER (WHERE outcome = 'TP'), count(*) FILTER (WHERE outcome = 'FP'), "
        f"count(*) FILTER (WHERE outcome = 'FN'), count(*) FILTER (WHERE outcome = 'pending') "
        f"FROM event_outcomes {where}").fetchone()
    precision = f"{tp / (tp + fp):.0%}" if tp + fp else "n/a"
    recall = f"{tp / (tp + fn):.0%}" if tp + fn else "n/a"
    print(f"TP {tp}   FP {fp}   FN {fn}   pending {waiting}")
    print(f"precision (buzzes that were real glasses): {precision}")
    print(f"recall    (glasses that got a buzz):       {recall}")
    rows = conn.execute(
        f"SELECT event_id, time, device_id, outcome, note FROM event_outcomes {where} "
        "ORDER BY time DESC LIMIT 15").fetchall()
    if rows:
        print("\nRecent")
        for event_id, time, dev, outcome, note in rows:
            print(f"{str(event_id)[:8]}  {time.astimezone():%Y-%m-%d %H:%M:%S}  {dev[:14]:14} {outcome:8} {note or ''}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("tp", "a buzz where glasses WERE present"), ("fp", "a buzz with NO glasses")):
        p = commands.add_parser(name, help=help_text)
        p.add_argument("id", nargs="?", help="event id prefix (default: latest pending buzz)")
        p.add_argument("--note")
    p = commands.add_parser("missed", help="glasses were present but there was no buzz (FN)")
    p.add_argument("--at", help="local time, e.g. '2026-11-07 14:03' (default: now)")
    p.add_argument("--device", default=device_id(), help="which wearable (default: this machine's name)")
    p.add_argument("--note")
    for name in ("pending", "report"):
        commands.add_parser(name).add_argument("--real", action="store_true", help="exclude demo events")
    args = parser.parse_args()

    url = service_url()
    if not url:
        raise SystemExit("No TIMESCALE_SERVICE_URL found (.env, $SIDE_EYE_ENV, or ../tiger-cloud-*-credentials.env).")
    with psycopg.connect(url, connect_timeout=10) as conn:
        if args.command in ("tp", "fp"):
            label(conn, args.id, args.command == "tp", args.note)
        elif args.command == "missed":
            missed(conn, args.at, args.device, args.note)
        elif args.command == "pending":
            pending(conn, args.real)
        else:
            report(conn, args.real)


if __name__ == "__main__":
    main()
