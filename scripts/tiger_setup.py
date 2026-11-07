"""Create the Side Eye alarm store on Tiger Data. Safe to re-run.

    python3 scripts/tiger_setup.py           # apply scripts/tiger_schema.sql
    python3 scripts/tiger_setup.py --smoke   # also insert, read back and delete one test alarm
"""

import argparse
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pi"))

import psycopg  # noqa: E402

from alarm_store import INSERT_SQL, COLUMNS, service_url  # noqa: E402


def statements(sql):
    # Our schema has no "--" or ";" inside string literals, so this simple split is enough.
    lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--smoke", action="store_true", help="round-trip one test alarm")
    args = parser.parse_args()
    url = service_url()
    if not url:
        raise SystemExit("No TIMESCALE_SERVICE_URL found (.env, $SIDE_EYE_ENV, or ../tiger-cloud-*-credentials.env).")
    # Continuous aggregates can't be created inside a transaction, so autocommit.
    with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
        for statement in statements((ROOT / "scripts" / "tiger_schema.sql").read_text()):
            conn.execute(statement)
        print("Schema applied: events (hypertable), event_outcomes (view), events_hourly "
              "(continuous aggregate), compression after 7 days, retention 30 days.")
        if args.smoke:
            event_id = uuid.uuid4()
            row = dict.fromkeys(COLUMNS)
            row.update(event_id=event_id, time=datetime.now(timezone.utc), device_id="smoke-test",
                       buzzed=True, source="pi", glasses_ref="smoke", signature="phone_simulator",
                       demo=True, rule="smoke")
            conn.execute(INSERT_SQL, tuple(row[c] for c in COLUMNS))
            found = conn.execute("SELECT count(*) FROM events WHERE event_id = %s", (event_id,)).fetchone()[0]
            conn.execute("DELETE FROM events WHERE event_id = %s", (event_id,))
            if found != 1:
                raise SystemExit("Smoke test failed: inserted alarm not found.")
            try:
                conn.execute("INSERT INTO events (event_id, time, device_id, buzzed, glasses_present, source) "
                             "VALUES (%s, now(), 'smoke-test', false, false, 'user')", (uuid.uuid4(),))
                raise SystemExit("Smoke test failed: a true negative (no buzz, no glasses) was accepted.")
            except psycopg.errors.CheckViolation:
                pass
            print("Smoke test passed: alarm round-trip OK, true negatives rejected.")


if __name__ == "__main__":
    main()
