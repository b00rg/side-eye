"""Integration tests against the real Tiger Data service.

Skipped unless you opt in, so normal test runs never touch the cloud:

    cd pi && SIDE_EYE_TIGER_TESTS=1 python3 -m unittest tests.test_tiger_integration -v

Every row is written under a unique device_id (test-xxxxxxxx) and deleted afterwards.
Run `python3 scripts/tiger_setup.py` first so the schema exists.
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from alarm_store import AlarmStore, GlassesRef, make_alarm, make_missed, service_url
from canary_core import Snapshot

ROOT = Path(__file__).resolve().parents[2]
ENABLED = os.environ.get("SIDE_EYE_TIGER_TESTS") == "1"
REASON = "set SIDE_EYE_TIGER_TESTS=1 (and have Tiger credentials + psycopg) to run"
SNAP = Snapshot(mean_rssi=-50.0, var_rssi=2.0, slope=0.3, dwell_s=7.0,
                close_streak_s=3.5, packet_rate_hz=9.0, latest_rssi=-49)


@unittest.skipUnless(ENABLED and importlib.util.find_spec("psycopg") and service_url(), REASON)
class TigerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.psycopg = psycopg
        cls.url = service_url()
        sys.path.insert(0, str(ROOT / "scripts"))
        import annotate

        cls.annotate = annotate

    def setUp(self):
        self.device = f"test-{uuid.uuid4().hex[:8]}"
        self.env = mock.patch.dict(os.environ, {"SIDE_EYE_DEVICE_ID": self.device})
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.buffer = Path(self.tmp.name) / "buffer.jsonl"
        self.conn = self.psycopg.connect(self.url, autocommit=True, connect_timeout=10)
        self.ref = GlassesRef()

    def tearDown(self):
        self.conn.execute("DELETE FROM events WHERE device_id = %s", (self.device,))
        self.conn.close()
        self.env.stop()
        self.tmp.cleanup()

    # helpers
    def store(self, connect=None):
        kwargs = {"connect": connect} if connect else {}
        return AlarmStore(self.url, buffer_path=self.buffer, **kwargs)

    def outcomes(self):
        return [row[0] for row in self.conn.execute(
            "SELECT outcome FROM event_outcomes WHERE device_id = %s ORDER BY time, outcome", (self.device,))]

    def count(self):
        return self.conn.execute("SELECT count(*) FROM events WHERE device_id = %s", (self.device,)).fetchone()[0]

    def insert_raw(self, buzzed, glasses_present):
        self.conn.execute(
            "INSERT INTO events (event_id, time, device_id, buzzed, glasses_present, source) "
            "VALUES (%s, now(), %s, %s, %s, 'test')", (uuid.uuid4(), self.device, buzzed, glasses_present))

    # schema
    def test_schema_objects_and_policies_exist(self):
        hypertables = {r[0] for r in self.conn.execute(
            "SELECT hypertable_name FROM timescaledb_information.hypertables")}
        self.assertIn("events", hypertables)
        caggs = {r[0] for r in self.conn.execute(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates")}
        self.assertIn("events_hourly", caggs)
        jobs = {(r[0], r[1]) for r in self.conn.execute(
            "SELECT proc_name, hypertable_name FROM timescaledb_information.jobs")}
        self.assertIn(("policy_retention", "events"), jobs)
        self.assertIn(("policy_compression", "events"), jobs)
        self.assertTrue(any(proc == "policy_refresh_continuous_aggregate" for proc, _ in jobs))

    def test_setup_script_is_safe_to_rerun(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "tiger_setup.py")],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)

    # the no-true-negative rule
    def test_database_rejects_true_negatives(self):
        with self.assertRaises(self.psycopg.errors.CheckViolation):
            self.insert_raw(buzzed=False, glasses_present=False)

    def test_database_rejects_unlabelled_non_buzzes(self):
        with self.assertRaises(self.psycopg.errors.CheckViolation):
            self.insert_raw(buzzed=False, glasses_present=None)

    def test_database_accepts_the_three_real_cases(self):
        self.insert_raw(True, None)
        self.insert_raw(True, True)
        self.insert_raw(True, False)
        self.insert_raw(False, True)
        self.assertEqual(sorted(self.outcomes()), ["FN", "FP", "TP", "pending"])

    # the uploader against the real database
    def test_store_uploads_buzzes_labels_and_misses(self):
        store = self.store()
        tp = make_alarm("AA:01", "phone_simulator", SNAP, 0.9, self.ref)
        fp = make_alarm("AA:02", "phone_simulator", SNAP, 0.4, self.ref)
        pending = make_alarm("AA:03", "phone_simulator", SNAP, None, self.ref)
        for row in (tp, fp, pending):
            store.record(row)
        store.label(tp, True)
        store.label(fp, False)
        store.record(make_missed(self.ref, "AA:04", "phone_simulator", SNAP, 0.1))
        store.close(timeout=30)
        self.assertEqual(sorted(self.outcomes()), ["FN", "FP", "TP", "pending"])
        rssi, ml = self.conn.execute(
            "SELECT rssi, ml_score FROM events WHERE event_id = %s", (tp["event_id"],)).fetchone()
        self.assertEqual(rssi, -49)
        self.assertAlmostEqual(ml, 0.9, places=4)

    def test_reuploading_the_same_alarm_creates_one_row(self):
        alarm = make_alarm("AA:01", "phone_simulator", SNAP, None, self.ref)
        store = self.store()
        store.record(alarm)
        store.record(alarm)
        store.close(timeout=30)
        self.assertEqual(self.count(), 1)

    def test_offline_buffer_reaches_the_database(self):
        def offline(url):
            raise OSError("simulated Wi-Fi drop")

        alarm = make_alarm("AA:01", "phone_simulator", SNAP, None, self.ref)
        with contextlib.redirect_stdout(io.StringIO()):
            store = self.store(connect=offline)
            store.record(alarm)
            store.label(alarm, True)
            store.close(timeout=30)
        self.assertTrue(self.buffer.exists())
        self.assertEqual(self.count(), 0)
        self.store().close(timeout=30)  # back online
        self.assertFalse(self.buffer.exists())
        self.assertEqual(self.outcomes(), ["TP"])

    def test_no_address_reaches_the_database(self):
        store = self.store()
        store.record(make_alarm("AA:BB:CC:DD:EE:FF", "phone_simulator", SNAP, None, self.ref))
        store.close(timeout=30)
        rows = self.conn.execute("SELECT * FROM events WHERE device_id = %s", (self.device,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("AA:BB:CC:DD:EE:FF", repr(rows))

    def test_hourly_rollup_counts_each_outcome(self):
        self.insert_raw(True, True)
        self.insert_raw(True, True)
        self.insert_raw(True, False)
        self.insert_raw(False, True)
        self.insert_raw(True, None)
        totals = self.conn.execute(
            "SELECT sum(buzzes), sum(tp), sum(fp), sum(fn), sum(pending) FROM events_hourly "
            "WHERE device_id = %s", (self.device,)).fetchone()
        self.assertEqual(tuple(int(v) for v in totals), (4, 2, 1, 1, 1))

    # scripts/annotate.py
    def test_annotate_labels_a_buzz_by_id_prefix(self):
        alarm = make_alarm("AA:01", "phone_simulator", SNAP, None, self.ref)
        store = self.store()
        store.record(alarm)
        store.close(timeout=30)
        with contextlib.redirect_stdout(io.StringIO()):
            self.annotate.label(self.conn, alarm["event_id"][:12], False, "phone in pocket")
        present, note = self.conn.execute(
            "SELECT glasses_present, note FROM events WHERE event_id = %s", (alarm["event_id"],)).fetchone()
        self.assertIs(present, False)
        self.assertEqual(note, "phone in pocket")

    def test_annotate_unknown_id_fails_cleanly(self):
        with self.assertRaises(SystemExit):
            self.annotate.label(self.conn, "zzzzzzzz", True, None)

    def test_annotate_missed_with_a_time(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.annotate.missed(self.conn, "2026-11-07 14:03", self.device, "walked past")
        row = self.conn.execute(
            "SELECT buzzed, glasses_present, source, note, extract(minute FROM time AT TIME ZONE 'UTC') "
            "FROM events WHERE device_id = %s", (self.device,)).fetchone()
        self.assertEqual(row[:4], (False, True, "user", "walked past"))

    def test_annotate_cli_end_to_end(self):
        env = {**os.environ, "SIDE_EYE_DEVICE_ID": self.device}
        script = str(ROOT / "scripts" / "annotate.py")
        missed = subprocess.run([sys.executable, script, "missed", "--note", "cli"],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(missed.returncode, 0, missed.stderr)
        self.assertIn("FN", missed.stdout)
        report = subprocess.run([sys.executable, script, "report"],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn("precision", report.stdout)
        self.assertEqual(self.outcomes(), ["FN"])


if __name__ == "__main__":
    unittest.main()
