"""Store Side Eye's training data in Tiger Data (TimescaleDB / PostgreSQL) and get it back out.

Usage (from the repo root; DATABASE_URL is Tiger Cloud's connection string, or a local TimescaleDB):
  export DATABASE_URL='postgres://user:pass@host:port/db?sslmode=require'
  python3 scripts/tiger.py init                        # create tables (hypertables for time series)
  python3 scripts/tiger.py load [--feedback feedback.db] [--mitre]
  python3 scripts/tiger.py export OUTDIR               # files the pipeline uses, rebuilt from the DB
  python3 scripts/tiger.py status

What goes in: our recorded walks (data/runs) and calibration (settings.json), NIST BLE RSSI readings,
simulated training rows (data/sim_rows.csv), and optionally MITRE readings and the anonymous feedback
rows from the Pi's button. Only training data: the live detector's readings never go here, and
feedback rows carry no device identifiers (see pi/feedback.py).

`export OUTDIR` writes OUTDIR/data/runs/*.csv, OUTDIR/settings.json, OUTDIR/sim_rows.csv and
OUTDIR/feedback.db in the same formats the Pi writes, so scripts/run_results.py and
pi/train_model.py run unchanged on data that came from the database.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import psycopg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROW_COLS = ["mean_rssi", "var_rssi", "slope", "dwell_s", "close_streak_s", "packet_rate_hz"]
FEATS = ["mean_rssi", "var_rssi", "slope", "dwell_s"]
NIST_NAME = re.compile(r"CTData_(N?LOS\d+)_(\d+)_O(\d)_(\d+)cm_Device(\d)\.csv$")

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE TABLE IF NOT EXISTS calibration (
    id SERIAL PRIMARY KEY, loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    near_median REAL, far_median REAL, threshold_dbm REAL NOT NULL);
CREATE TABLE IF NOT EXISTS walks (
    run_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL,
    n_rows INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS walk_rows (
    time TIMESTAMPTZ NOT NULL, run_id TEXT NOT NULL REFERENCES walks(run_id) ON DELETE CASCADE,
    t DOUBLE PRECISION NOT NULL, mean_rssi REAL, var_rssi REAL, slope REAL, dwell_s REAL,
    close_streak_s REAL, packet_rate_hz REAL, label SMALLINT NOT NULL);
SELECT create_hypertable('walk_rows', 'time', if_not_exists => TRUE);
CREATE TABLE IF NOT EXISTS public_readings (
    time TIMESTAMPTZ NOT NULL, dataset TEXT NOT NULL, scenario TEXT, sub TEXT,
    distance_cm INTEGER, device TEXT, rssi SMALLINT NOT NULL);
SELECT create_hypertable('public_readings', 'time', if_not_exists => TRUE);
CREATE TABLE IF NOT EXISTS sim_rows (
    id SERIAL PRIMARY KEY, mean_rssi REAL, var_rssi REAL, slope REAL, dwell_s REAL, label SMALLINT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback_rows (
    time TIMESTAMPTZ NOT NULL, seq INTEGER NOT NULL, source TEXT NOT NULL, mean_rssi REAL, var_rssi REAL, slope REAL, dwell_s REAL,
    label SMALLINT NOT NULL, weight REAL NOT NULL);
SELECT create_hypertable('feedback_rows', 'time', if_not_exists => TRUE);
"""


def connect(url):
    if not url:
        sys.exit("Set DATABASE_URL (Tiger Cloud connection string, or a local TimescaleDB).")
    return psycopg.connect(url, autocommit=False)


def started_at(run_id):
    m = re.match(r"(\d{8})-(\d{6})", run_id)
    return datetime.strptime("".join(m.groups()), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)


def copy_rows(cur, table, cols, rows):
    with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


def load_walks(cur):
    runs = os.path.join(ROOT, "data", "runs")
    settings = json.load(open(os.path.join(ROOT, "settings.json")))
    cur.execute("INSERT INTO calibration (near_median, far_median, threshold_dbm) VALUES (%s, %s, %s)",
                (settings.get("near_median"), settings.get("far_median"), settings["threshold_dbm"]))
    files = sorted(glob.glob(os.path.join(runs, "*.csv")))
    for path in files:
        d = pd.read_csv(path)
        run_id = d.run_id.iloc[0]
        t0, start = d.t.iloc[0], started_at(run_id)
        cur.execute("DELETE FROM walks WHERE run_id = %s", (run_id,))
        cur.execute("INSERT INTO walks VALUES (%s, %s, %s, %s)", (run_id, d.scenario.iloc[0], start, len(d)))
        copy_rows(cur, "walk_rows", ["time", "run_id", "t", *ROW_COLS, "label"],
                  [(start + timedelta(seconds=float(r.t - t0)), run_id, float(r.t),
                    *[float(r[c]) for c in ROW_COLS], int(r.label)) for _, r in d.iterrows()])
    return f"{len(files)} walks"


def load_nist(cur):
    root = os.path.join(ROOT, "data", "external", "nist", "BLE_RSSI_Data")
    cur.execute("DELETE FROM public_readings WHERE dataset = 'NIST'")
    n = 0
    for path in glob.glob(os.path.join(root, "*", "*", "*.csv")):
        m = NIST_NAME.search(os.path.basename(path))
        if not m:
            continue
        scen, sub, orient, cm, dev = m.groups()
        d = pd.read_csv(path, usecols=["Timestamp_ms", "RSSI_dBm"])
        copy_rows(cur, "public_readings", ["time", "dataset", "scenario", "sub", "distance_cm", "device", "rssi"],
                  [(datetime.fromtimestamp(ts / 1000, timezone.utc), "NIST", scen, f"{sub}/O{orient}", int(cm),
                    f"Device{dev}", int(r)) for ts, r in zip(d.Timestamp_ms, d.RSSI_dBm)])
        n += len(d)
    return f"{n} NIST readings"


def load_mitre(cur):
    d = pd.read_csv(os.path.join(ROOT, "data", "external", "mitre_rssi.csv"))
    d = d[d.tx_android & (d.rssi < 0)]
    cur.execute("DELETE FROM public_readings WHERE dataset = 'MITRE'")
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)   # MITRE logs only relative time per file
    copy_rows(cur, "public_readings", ["time", "dataset", "scenario", "sub", "distance_cm", "device", "rssi"],
              [(base + timedelta(seconds=float(t)), "MITRE", s, f, int(round(cm * 30.48)), dev, int(r))
               for t, s, f, cm, dev, r in zip(d.t_s, d.set, d.file, d.range_ft, d.tx_device, d.rssi)])
    return f"{len(d)} MITRE readings"


def load_sim(cur):
    path = os.path.join(ROOT, "data", "sim_rows.csv")
    if not os.path.exists(path):
        return "no data/sim_rows.csv (run scripts/deploy_model3.py or nist/export_sim_rows.py first)"
    d = pd.read_csv(path)
    cur.execute("TRUNCATE sim_rows")
    copy_rows(cur, "sim_rows", [*FEATS, "label"], [(*map(float, r[FEATS]), int(r.label)) for _, r in d.iterrows()])
    return f"{len(d)} simulated rows"


def load_feedback(cur, path):
    """Copy feedback rows not synced yet, then mark them synced in the Pi's feedback.db."""
    db = sqlite3.connect(path)
    rows = db.execute("SELECT id, minute, source, mean_rssi, var_rssi, slope, dwell_s, label, weight "
                      "FROM feedback_rows WHERE synced = 0").fetchall()
    copy_rows(cur, "feedback_rows", ["time", "seq", "source", *FEATS, "label", "weight"],
              [(datetime.fromtimestamp(r[1], timezone.utc), r[0], *r[2:]) for r in rows])
    return db, [r[0] for r in rows]


def cmd_init(url):
    with connect(url) as conn:
        conn.execute(SCHEMA)
    print("Tables ready: calibration, walks, walk_rows*, public_readings*, sim_rows, feedback_rows* (* = hypertable)")


def cmd_load(url, feedback, mitre, sim_only=False):
    if sim_only:
        with connect(url) as conn, conn.cursor() as cur:
            print("Loaded:", load_sim(cur))
        return
    with connect(url) as conn, conn.cursor() as cur:
        print("Loaded:", load_walks(cur))
        print("Loaded:", load_nist(cur))
        if mitre:
            print("Loaded:", load_mitre(cur))
        print("Loaded:", load_sim(cur))
        fb = None
        if feedback:
            fb, ids = load_feedback(cur, feedback)
            print(f"Loaded: {len(ids)} new feedback rows (anonymous: features, label, weight, minute)")
        conn.commit()
    if fb:   # only mark synced once the cloud copy is committed
        fb.executemany("UPDATE feedback_rows SET synced = 1 WHERE id = ?", [(i,) for i in ids])
        fb.commit()


def cmd_export(url, out):
    os.makedirs(os.path.join(out, "data", "runs"), exist_ok=True)
    with connect(url) as conn:
        cal = conn.execute("SELECT near_median, far_median, threshold_dbm FROM calibration "
                           "ORDER BY id DESC LIMIT 1").fetchone()
        with open(os.path.join(out, "settings.json"), "w") as f:
            json.dump({"threshold_dbm": cal[2], "near_median": cal[0], "far_median": cal[1]}, f, indent=2)
            f.write("\n")
        walks = conn.execute("SELECT run_id, scenario FROM walks ORDER BY run_id").fetchall()
        for run_id, scenario in walks:
            d = pd.DataFrame(conn.execute(f"SELECT t, {', '.join(ROW_COLS)}, label FROM walk_rows WHERE run_id = %s "
                                          "ORDER BY time", (run_id,)).fetchall(), columns=["t", *ROW_COLS, "label"])
            d.insert(0, "scenario", scenario)
            d.insert(0, "run_id", run_id)
            d.to_csv(os.path.join(out, "data", "runs", f"{run_id}.csv"), index=False)
        sim = pd.DataFrame(conn.execute(f"SELECT {', '.join(FEATS)}, label FROM sim_rows ORDER BY id").fetchall(),
                           columns=[*FEATS, "label"])
        sim.to_csv(os.path.join(out, "sim_rows.csv"), index=False)
        fb_rows = conn.execute(f"SELECT extract(epoch FROM time)::bigint, source, {', '.join(FEATS)}, label, weight "
                               "FROM feedback_rows ORDER BY time, seq").fetchall()
    sys.path.insert(0, os.path.join(ROOT, "pi"))
    from feedback import FeedbackStore
    fb_path = os.path.join(out, "feedback.db")
    if os.path.exists(fb_path):
        os.remove(fb_path)
    store = FeedbackStore(fb_path)
    for minute, source, *rest in fb_rows:
        store.add([rest[:4]], rest[4], rest[5], source, wall_time=minute)
    store.db.close()
    print(f"Exported {len(walks)} walks, calibration (cutoff {cal[2]}), {len(sim)} simulated rows, "
          f"{len(fb_rows)} feedback rows to {out}")


def cmd_status(url):
    with connect(url) as conn:
        for table in ("calibration", "walks", "walk_rows", "public_readings", "sim_rows", "feedback_rows"):
            n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            print(f"  {table:16s} {n:>9,} rows")
        for ds, n in conn.execute("SELECT dataset, count(*) FROM public_readings GROUP BY dataset").fetchall():
            print(f"    public_readings[{ds}] {n:,}")
        hyper = [r[0] for r in conn.execute("SELECT hypertable_name FROM timescaledb_information.hypertables")]
        print("  hypertables:", ", ".join(sorted(hyper)))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL"), help="defaults to $DATABASE_URL")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    load = sub.add_parser("load")
    load.add_argument("--feedback", help="the Pi's feedback.db: sync its anonymous rows (off unless given)")
    load.add_argument("--sim-only", action="store_true", help="only replace the simulated rows (data/sim_rows.csv)")
    load.add_argument("--mitre", action="store_true", help="also load MITRE Android-transmitter readings (~17k rows)")
    export = sub.add_parser("export")
    export.add_argument("out")
    sub.add_parser("status")
    args = ap.parse_args()
    if args.cmd == "init":
        cmd_init(args.db)
    elif args.cmd == "load":
        cmd_load(args.db, args.feedback, args.mitre, args.sim_only)
    elif args.cmd == "export":
        cmd_export(args.db, args.out)
    else:
        cmd_status(args.db)


if __name__ == "__main__":
    main()
