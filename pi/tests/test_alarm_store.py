"""Unit tests for pi/alarm_store.py: rows, privacy, env loading, upload thread, offline buffer.

No network: every test uses a fake database connection.
"""

import json
import os
import tempfile
import threading
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import alarm_store
from alarm_store import (
    COLUMNS, INSERT_SQL, LABEL_SQL, AlarmStore, GlassesRef, _row, make_alarm, make_missed,
    read_env_file, service_url,
)
from canary_core import Snapshot


SNAP = Snapshot(mean_rssi=-52.1, var_rssi=3.2, slope=0.4, dwell_s=6.0,
                close_streak_s=3.4, packet_rate_hz=9.0, latest_rssi=-50)
ADDRESS = "AA:BB:CC:DD:EE:FF"


class FakeDatabase:
    """Records executed statements. `down` simulates the network being unavailable."""

    def __init__(self):
        self.down = False
        self.executed = []  # (kind, params) in commit order
        self.connects = 0

    def connect(self, url):
        if self.down:
            raise OSError("network down")
        self.connects += 1
        return FakeConnection(self)

    def kinds(self):
        return [kind for kind, _ in self.executed]


class FakeConnection:
    def __init__(self, db):
        self.db = db
        self.pending = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        if self.db.down:
            raise OSError("network down")
        assert sql in (INSERT_SQL, LABEL_SQL), sql
        self.pending.append(("insert" if sql == INSERT_SQL else "label", params))

    def commit(self):
        if self.db.down:
            raise OSError("network down")
        self.db.executed.extend(self.pending)
        self.pending = []

    def close(self):
        pass


def wait_for(condition, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.buffer = Path(self.tmp.name) / "buffer.jsonl"
        self.db = FakeDatabase()

    def tearDown(self):
        self.tmp.cleanup()

    def new_store(self):
        return AlarmStore("postgres://fake", buffer_path=self.buffer, connect=self.db.connect)

    def buffered(self):
        if not self.buffer.exists():
            return []
        return [json.loads(line) for line in self.buffer.read_text().splitlines()]


class RowTests(unittest.TestCase):
    def test_alarm_row_has_every_column_and_is_pending(self):
        alarm = make_alarm(ADDRESS, "phone_simulator", SNAP, 0.81234, GlassesRef())
        self.assertEqual(set(alarm), set(COLUMNS))
        self.assertTrue(alarm["buzzed"])
        self.assertIsNone(alarm["glasses_present"])
        self.assertIsNone(alarm["annotated_at"])
        self.assertEqual(alarm["source"], "pi")
        self.assertEqual(alarm["rule"], "3s-streak")
        self.assertEqual(alarm["rssi"], -50)
        self.assertEqual(alarm["close_streak_s"], 3.4)
        self.assertEqual(alarm["ml_score"], 0.8123)

    def test_shape_inputs_are_stored_with_a_shape_model_buzz(self):
        alarm = make_alarm(ADDRESS, "phone_simulator", SNAP, 0.9, GlassesRef(), rule="shape-model",
                           shape=[8.123456, 0.0, 1.5, 6.0])
        self.assertEqual(alarm["rule"], "shape-model")
        self.assertEqual((alarm["rise"], alarm["drop"], alarm["steady"], alarm["held"]), (8.1235, 0.0, 1.5, 6.0))
        self.assertEqual(alarm["mean_rssi"], -52.1)   # the level inputs are still there

    def test_shape_inputs_are_null_without_the_shape_model(self):
        for row in (make_alarm(ADDRESS, "x", SNAP, None, GlassesRef()), make_missed(GlassesRef()),
                    make_missed(GlassesRef(), ADDRESS, "x", SNAP, 0.2)):
            for key in ("rise", "drop", "steady", "held"):
                self.assertIsNone(row[key], key)

    def test_missed_report_carries_shape_inputs(self):
        row = make_missed(GlassesRef(), ADDRESS, "x", SNAP, 0.4, shape=[5.0, 1.0, 2.0, 3.0])
        self.assertEqual(row["held"], 3.0)
        self.assertTrue(row["glasses_present"])

    def test_demo_flag_and_signature_fallback(self):
        self.assertTrue(make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef())["demo"])
        real = make_alarm(ADDRESS, "company_0x058e", SNAP, None, GlassesRef())
        self.assertFalse(real["demo"])
        self.assertIsNone(real["ml_score"])
        self.assertEqual(make_alarm(ADDRESS, None, SNAP, None, GlassesRef())["signature"], "unknown")

    def test_each_alarm_gets_a_unique_id(self):
        ref = GlassesRef()
        ids = {make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref)["event_id"] for _ in range(50)}
        self.assertEqual(len(ids), 50)

    def test_missed_report_is_a_false_negative(self):
        row = make_missed(GlassesRef(), ADDRESS, "company_0x058e", SNAP, 0.2)
        self.assertEqual(set(row), set(COLUMNS))
        self.assertFalse(row["buzzed"])
        self.assertTrue(row["glasses_present"])
        self.assertIsNotNone(row["annotated_at"])
        self.assertEqual(row["source"], "button")
        self.assertIsNone(row["rule"])
        self.assertEqual(row["mean_rssi"], -52.1)

    def test_missed_report_without_a_tracked_device_has_no_features(self):
        row = make_missed(GlassesRef(), source="user", note="on the bus")
        self.assertIsNone(row["glasses_ref"])
        self.assertIsNone(row["signature"])
        self.assertFalse(row["demo"])
        self.assertEqual(row["note"], "on the bus")
        for key in ("rssi", "mean_rssi", "var_rssi", "slope", "dwell_s", "close_streak_s", "ml_score"):
            self.assertIsNone(row[key], key)

    def test_rows_satisfy_the_no_true_negative_rule(self):
        # Mirrors the database CHECK (buzzed OR glasses_present IS TRUE).
        for row in (make_alarm(ADDRESS, "x", SNAP, None, GlassesRef()), make_missed(GlassesRef())):
            self.assertTrue(row["buzzed"] or row["glasses_present"] is True)

    def test_row_tuple_follows_column_order_and_parses_times(self):
        alarm = make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef())
        values = _row(alarm)
        self.assertEqual(len(values), len(COLUMNS))
        self.assertEqual(values[COLUMNS.index("event_id")], alarm["event_id"])
        parsed = values[COLUMNS.index("time")]
        self.assertIsInstance(parsed, datetime)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertIsNone(values[COLUMNS.index("annotated_at")])
        missed = _row(make_missed(GlassesRef()))
        self.assertIsInstance(missed[COLUMNS.index("annotated_at")], datetime)

    def test_rows_survive_a_json_round_trip(self):
        # The offline buffer stores rows as JSON; they must come back identical.
        alarm = make_alarm(ADDRESS, "phone_simulator", SNAP, 0.5, GlassesRef())
        self.assertEqual(_row(json.loads(json.dumps(alarm))), _row(alarm))


class PrivacyTests(unittest.TestCase):
    ADDRESS_FORMS = (ADDRESS, ADDRESS.lower(), ADDRESS.replace(":", ""), ADDRESS.replace(":", "").lower())

    def assert_no_address(self, row):
        text = json.dumps(row)
        for form in self.ADDRESS_FORMS:
            self.assertNotIn(form, text)

    def test_no_row_contains_the_ble_address(self):
        ref = GlassesRef()
        self.assert_no_address(make_alarm(ADDRESS, "phone_simulator", SNAP, 0.9, ref))
        self.assert_no_address(make_missed(ref, ADDRESS, "phone_simulator", SNAP, 0.1))

    def test_glasses_ref_is_stable_within_a_day(self):
        ref = GlassesRef(today=lambda: date(2026, 11, 7))
        self.assertEqual(ref(ADDRESS), ref(ADDRESS))
        self.assertNotEqual(ref(ADDRESS), ref("11:22:33:44:55:66"))
        self.assertEqual(len(ref(ADDRESS)), 16)

    def test_glasses_ref_rotates_at_midnight(self):
        today = [date(2026, 11, 7)]
        ref = GlassesRef(today=lambda: today[0])
        before = ref(ADDRESS)
        today[0] = date(2026, 11, 8)
        self.assertNotEqual(before, ref(ADDRESS))

    def test_glasses_ref_rotates_on_restart(self):
        # A new process gets a new random key, so refs can't be linked across restarts.
        fixed = lambda: date(2026, 11, 7)  # noqa: E731
        self.assertNotEqual(GlassesRef(today=fixed)(ADDRESS), GlassesRef(today=fixed)(ADDRESS))


class EnvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        clean = {k: v for k, v in os.environ.items() if k not in ("TIMESCALE_SERVICE_URL", "SIDE_EYE_ENV")}
        self.env = mock.patch.dict(os.environ, clean, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def write(self, name, url):
        path = self.dir / name
        path.write_text(f"TIMESCALE_SERVICE_URL={url}\n")
        return path

    def test_parses_the_tiger_cloud_file_format(self):
        path = self.dir / "tiger.env"
        path.write_text(
            "# This file contains environmental variables\n"
            "TIMESCALE_SERVICE_URL=postgres://user:pw@host:31572/tsdb?sslmode=require\n"
            "# _ = load_dotenv(find_dotenv())\n"
            "# TIMESCALE_SERVICE_URL=postgres://commented-out\n"
            "\n"
            "PGPORT='31572'\n"
            'PGUSER="tsdbadmin"\n'
        )
        values = read_env_file(path)
        self.assertEqual(values["TIMESCALE_SERVICE_URL"], "postgres://user:pw@host:31572/tsdb?sslmode=require")
        self.assertEqual(values["PGPORT"], "31572")
        self.assertEqual(values["PGUSER"], "tsdbadmin")
        self.assertNotIn("_", values)

    def test_environment_variable_wins(self):
        os.environ["TIMESCALE_SERVICE_URL"] = "postgres://from-env"
        os.environ["SIDE_EYE_ENV"] = str(self.write("x.env", "postgres://from-file"))
        self.assertEqual(service_url(), "postgres://from-env")

    def test_side_eye_env_beats_default_files(self):
        default = self.write("default.env", "postgres://default")
        os.environ["SIDE_EYE_ENV"] = str(self.write("chosen.env", "postgres://chosen"))
        with mock.patch.object(alarm_store, "ENV_CANDIDATES", (default,)):
            self.assertEqual(service_url(), "postgres://chosen")

    def test_default_files_in_order(self):
        first = self.dir / "missing.env"
        second = self.write("second.env", "postgres://second")
        third = self.write("third.env", "postgres://third")
        with mock.patch.object(alarm_store, "ENV_CANDIDATES", (first, second, third)):
            self.assertEqual(service_url(), "postgres://second")

    def test_no_credentials_means_no_store(self):
        with mock.patch.object(alarm_store, "ENV_CANDIDATES", (self.dir / "missing.env",)), \
                mock.patch("builtins.print"):
            self.assertIsNone(service_url())
            self.assertIsNone(AlarmStore.from_env())


class UploadTests(StoreTestCase):
    def test_buzz_is_uploaded(self):
        store = self.new_store()
        alarm = make_alarm(ADDRESS, "company_0x058e", SNAP, None, GlassesRef())
        store.record(alarm)
        store.close()
        self.assertEqual(self.db.kinds(), ["insert"])
        self.assertEqual(self.db.executed[0][1][COLUMNS.index("event_id")], alarm["event_id"])
        self.assertFalse(self.buffer.exists())

    def test_label_sends_the_right_values(self):
        store = self.new_store()
        alarm = make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef())
        store.record(alarm)
        store.label(alarm, glasses_present=True)
        store.close()
        self.assertEqual(self.db.kinds(), ["insert", "label"])
        present, annotated_at, event_id, when = self.db.executed[1][1]
        self.assertIs(present, True)
        self.assertIsInstance(annotated_at, datetime)
        self.assertEqual(event_id, alarm["event_id"])
        self.assertEqual(when, datetime.fromisoformat(alarm["time"]))

    def test_operations_keep_their_order(self):
        store = self.new_store()
        ref = GlassesRef()
        a = make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref)
        b = make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref)
        store.record(a)
        store.label(a, True)
        store.record(make_missed(ref))
        store.record(b)
        store.label(b, False)
        store.close()
        self.assertEqual(self.db.kinds(), ["insert", "label", "insert", "insert", "label"])

    def test_record_never_blocks_on_a_slow_network(self):
        release = threading.Event()

        def slow_connect(url):
            release.wait(5)
            return self.db.connect(url)

        store = AlarmStore("postgres://fake", buffer_path=self.buffer, connect=slow_connect)
        ref = GlassesRef()
        started = time.monotonic()
        for _ in range(100):
            store.record(make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref))
        self.assertLess(time.monotonic() - started, 0.5)
        release.set()
        store.close()
        self.assertEqual(len(self.db.executed), 100)

    def test_connection_is_reused(self):
        store = self.new_store()
        ref = GlassesRef()
        for _ in range(5):
            store.record(make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref))
        store.close()
        self.assertEqual(self.db.connects, 1)


class OfflineBufferTests(StoreTestCase):
    def test_offline_operations_are_buffered_in_order(self):
        self.db.down = True
        store = self.new_store()
        alarm = make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef())
        with mock.patch("builtins.print"):
            store.record(alarm)
            store.label(alarm, False)
            store.close()
        self.assertEqual(self.db.executed, [])
        self.assertEqual([op["op"] for op in self.buffered()], ["insert", "label"])
        self.assertEqual(self.buffered()[1]["event_id"], alarm["event_id"])

    def test_buffer_is_replayed_before_new_operations(self):
        self.db.down = True
        store = self.new_store()
        old = make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef())
        with mock.patch("builtins.print"):
            store.record(old)
            store.label(old, True)
            store.close()
        self.db.down = False
        store = self.new_store()
        new = make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef())
        store.record(new)
        store.close()
        self.assertEqual(self.db.kinds(), ["insert", "label", "insert"])
        ids = [params[COLUMNS.index("event_id")] for kind, params in self.db.executed if kind == "insert"]
        self.assertEqual(ids, [old["event_id"], new["event_id"]])
        self.assertFalse(self.buffer.exists())

    def test_buffer_is_flushed_on_shutdown_without_new_alarms(self):
        self.db.down = True
        store = self.new_store()
        with mock.patch("builtins.print"):
            store.record(make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef()))
            store.close()
        self.db.down = False
        self.new_store().close()
        self.assertEqual(self.db.kinds(), ["insert"])
        self.assertFalse(self.buffer.exists())

    def test_buffer_is_retried_while_idle(self):
        self.db.down = True
        with mock.patch.object(alarm_store, "RETRY_SECONDS", 0.05), mock.patch("builtins.print"):
            store = self.new_store()
            store.record(make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef()))
            self.assertTrue(wait_for(self.buffer.exists))
            self.db.down = False  # Wi-Fi comes back; no new alarm arrives
            self.assertTrue(wait_for(lambda: self.db.kinds() == ["insert"]))
            self.assertFalse(self.buffer.exists())
            store.close()

    def test_failed_replay_loses_nothing(self):
        self.db.down = True
        store = self.new_store()
        ref = GlassesRef()
        with mock.patch("builtins.print"):
            for _ in range(3):
                store.record(make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref))
            store.close()
            store = self.new_store()  # still offline: replay fails, new alarm appended
            store.record(make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref))
            store.close()
        self.assertEqual(len(self.buffered()), 4)
        self.db.down = False
        self.new_store().close()
        self.assertEqual(len(self.db.executed), 4)

    def test_network_dropping_mid_session(self):
        store = self.new_store()
        ref = GlassesRef()
        with mock.patch("builtins.print"):
            store.record(make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref))
            self.assertTrue(wait_for(lambda: len(self.db.executed) == 1))
            self.db.down = True
            store.record(make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref))
            self.assertTrue(wait_for(self.buffer.exists))
            self.db.down = False
            store.record(make_alarm(ADDRESS, "phone_simulator", SNAP, None, ref))
            store.close()
        self.assertEqual(len(self.db.executed), 3)
        self.assertFalse(self.buffer.exists())

    def test_legacy_buffer_lines_are_replayed_as_inserts(self):
        # Lines written before operations were tagged held the bare row.
        alarm = make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef())
        self.buffer.write_text(json.dumps(alarm) + "\n")
        self.new_store().close()
        self.assertEqual(self.db.kinds(), ["insert"])
        self.assertEqual(self.db.executed[0][1][COLUMNS.index("event_id")], alarm["event_id"])

    def test_blank_lines_in_buffer_are_ignored(self):
        alarm = make_alarm(ADDRESS, "phone_simulator", SNAP, None, GlassesRef())
        self.buffer.write_text("\n" + json.dumps({"op": "insert", "row": alarm}) + "\n\n")
        self.new_store().close()
        self.assertEqual(self.db.kinds(), ["insert"])


if __name__ == "__main__":
    unittest.main()
