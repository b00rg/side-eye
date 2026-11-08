"""End-to-end tests of `canary.py live` with simulated hardware.

The real live() loop runs unchanged. What is faked:
- the clock (each `asyncio.sleep(1)` advances simulated time by exactly 1 s)
- the BLE scanner (a phone-simulator packet every 0.2 s at whatever RSSI the test says)
- the motor, the wire button and the Tiger Data store (they just record what happened)

Times in these tests are simulated seconds since live() started.
"""

import asyncio
import contextlib
import io
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import alarm_store
import canary
from canary_core import BUTTON_WINDOW_S, DEMO_ID, DEMO_PAYLOAD


ADDRESS = "AA:BB:CC:DD:EE:FF"
THRESHOLD = -60
T0 = 1000.0
PACKET_EVERY = 0.2
CLOSE, FAR = -50, -75
REAL_SESSION = canary.Session


class StopLive(Exception):
    pass


class FakeMotor:
    def __init__(self):
        self.events = []
        self.closed = False

    async def pulse(self):
        self.events.append("alarm")

    async def tick(self, count):
        self.events.append(f"tick{count}")

    def close(self):
        self.closed = True


class FakeButton:
    def __init__(self):
        self.durations = deque()
        self.closed = False

    def presses(self):
        while self.durations:
            yield self.durations.popleft()

    def close(self):
        self.closed = True


class FakeStore:
    def __init__(self, clock):
        self.clock = clock
        self.records = []  # (seconds since start, row)
        self.labels = []   # (seconds since start, row, glasses_present)
        self.closed = False

    def record(self, row):
        self.records.append((self.clock() - T0, row))

    def label(self, row, glasses_present):
        self.labels.append((self.clock() - T0, row, glasses_present))

    def close(self):
        self.closed = True

    def buzzes(self):
        return [row for _, row in self.records if row["buzzed"]]

    def missed(self):
        return [row for _, row in self.records if not row["buzzed"]]


class FakeScanner:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class LiveRun:
    """Run live() in simulated time.

    rssi_at(t) -> RSSI in dBm, or None for no packet at second t.
    presses    -> {second: touch duration}; the touch is seen at that second's tick.
    """

    def __init__(self, rssi_at, presses=None, seconds=30, with_motor=True, with_db=True,
                 with_button=True, store_available=True, button_fails=False, model_dir=None):
        self.rssi_at = rssi_at
        self.presses = presses or {}
        self.seconds = seconds
        self.flags = (with_motor, with_db, with_button)
        self.now = T0
        self.session = None
        self.motor = FakeMotor()
        self.button = FakeButton()
        self.buttons_made = 0
        self.button_fails = button_fails
        self.store = FakeStore(lambda: self.now) if store_available else None
        self.model_dir = model_dir
        self.output = ""

    def _make_session(self):
        self.session = REAL_SESSION()
        return self.session

    def _make_button(self):
        self.buttons_made += 1
        if self.button_fails:
            raise RuntimeError("no GPIO")
        return self.button

    async def _sleep(self, seconds):
        start, end = self.now, self.now + seconds
        step = 1
        while True:
            t = round(start + step * PACKET_EVERY, 6)
            if t > end + 1e-9:
                break
            rssi = self.rssi_at(t - T0)
            if rssi is not None:
                self.now = t
                advertisement = SimpleNamespace(
                    manufacturer_data={DEMO_ID: DEMO_PAYLOAD}, service_uuids=[], rssi=rssi)
                self.session.callback(SimpleNamespace(address=ADDRESS), advertisement)
            step += 1
        self.now = end
        elapsed = round(end - T0)
        if elapsed in self.presses:
            self.button.durations.append(self.presses[elapsed])
        if elapsed > self.seconds:
            raise StopLive

    def run(self):
        root = self.model_dir or tempfile.mkdtemp()
        patches = [
            mock.patch.object(canary, "asyncio", SimpleNamespace(sleep=self._sleep)),
            mock.patch.object(canary, "time", SimpleNamespace(monotonic=lambda: self.now)),
            mock.patch.object(canary, "Session", self._make_session),
            mock.patch.object(canary, "new_scanner", lambda callback: FakeScanner()),
            mock.patch.object(canary, "threshold", lambda: THRESHOLD),
            mock.patch.object(canary, "Motor", lambda: self.motor),
            mock.patch.object(canary, "WireButton", self._make_button),
            mock.patch.object(canary, "ROOT", Path(root)),
            mock.patch.object(alarm_store.AlarmStore, "from_env", lambda: self.store),
        ]
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            try:
                asyncio.run(canary.live(*self.flags))
            except StopLive:
                pass
        self.output = out.getvalue()
        return self


def constant(rssi, until=None, start=0):
    return lambda t: rssi if t >= start and (until is None or t <= until) else None


def nothing(t):
    return None


class BuzzTests(unittest.TestCase):
    def test_close_device_buzzes_after_about_three_seconds(self):
        run = LiveRun(constant(CLOSE), seconds=10).run()
        self.assertEqual(len(run.store.buzzes()), 1)
        when, alarm = run.store.records[0]
        self.assertTrue(3 <= when <= 5, when)
        self.assertGreaterEqual(alarm["close_streak_s"], 3.0)
        self.assertEqual(alarm["signature"], "phone_simulator")
        self.assertTrue(alarm["demo"])
        self.assertEqual(run.motor.events, ["alarm"])

    def test_cooldown_limits_buzzes_to_one_per_15_seconds(self):
        run = LiveRun(constant(CLOSE), seconds=40).run()
        times = [when for when, _ in run.store.records]
        self.assertEqual(len(times), 3, times)
        for earlier, later in zip(times, times[1:]):
            self.assertGreaterEqual(later - earlier, 15)
        self.assertEqual(run.motor.events.count("alarm"), 3)

    def test_far_device_never_buzzes(self):
        run = LiveRun(constant(FAR), seconds=30).run()
        self.assertEqual(run.store.records, [])
        self.assertEqual(run.motor.events, [])

    def test_passer_by_shorter_than_three_seconds_never_buzzes(self):
        run = LiveRun(constant(CLOSE, start=5, until=7), seconds=20).run()
        self.assertEqual(run.store.records, [])

    def test_no_packets_means_nothing_stored(self):
        # No glasses and no buzz: a true negative, so nothing at all reaches the database.
        run = LiveRun(nothing, seconds=30).run()
        self.assertEqual(run.store.records, [])
        self.assertEqual(run.store.labels, [])
        self.assertIn("Waiting for simulator packet", run.output)

    def test_uploaded_alarm_never_contains_the_address(self):
        run = LiveRun(constant(CLOSE), seconds=10).run()
        self.assertNotIn(ADDRESS, repr(run.store.records))
        self.assertEqual(len(run.store.buzzes()[0]["glasses_ref"]), 16)


class ButtonTests(unittest.TestCase):
    def buzz_time(self, run):
        return run.store.records[0][0]

    def test_tap_after_buzz_marks_true_positive(self):
        run = LiveRun(constant(CLOSE, until=8), presses={7: 0.3}, seconds=12).run()
        self.assertEqual(len(run.store.buzzes()), 1)
        self.assertEqual(len(run.store.labels), 1)
        _, row, present = run.store.labels[0]
        self.assertIs(present, True)
        self.assertEqual(row["event_id"], run.store.buzzes()[0]["event_id"])
        self.assertEqual(run.motor.events, ["alarm", "tick1"])
        self.assertEqual(run.store.missed(), [])

    def test_hold_after_buzz_marks_false_positive(self):
        run = LiveRun(constant(CLOSE, until=8), presses={7: 2.0}, seconds=12).run()
        self.assertEqual([present for _, _, present in run.store.labels], [False])
        self.assertEqual(run.motor.events, ["alarm", "tick2"])

    def test_tap_with_nothing_around_reports_a_bare_miss(self):
        run = LiveRun(nothing, presses={5: 0.3}, seconds=8).run()
        self.assertEqual(run.store.labels, [])
        [missed] = run.store.missed()
        self.assertTrue(missed["glasses_present"])
        self.assertIsNone(missed["glasses_ref"])
        self.assertIsNone(missed["rssi"])
        self.assertEqual(run.motor.events, ["tick3"])

    def test_tap_with_a_quiet_device_attaches_its_signal(self):
        # The Pi sees the device but it's below the threshold, so it never buzzed.
        run = LiveRun(constant(FAR), presses={6: 0.3}, seconds=8).run()
        self.assertEqual(run.store.buzzes(), [])
        [missed] = run.store.missed()
        self.assertEqual(missed["signature"], "phone_simulator")
        self.assertEqual(missed["rssi"], FAR)
        self.assertAlmostEqual(missed["mean_rssi"], FAR, delta=1)
        self.assertEqual(missed["close_streak_s"], 0)
        self.assertEqual(len(missed["glasses_ref"]), 16)
        self.assertNotIn(ADDRESS, repr(missed))

    def test_tap_after_the_window_is_a_miss_not_a_label(self):
        run = LiveRun(constant(CLOSE, until=6), presses={30: 0.3}, seconds=32).run()
        self.assertGreater(30 - self.buzz_time(run), BUTTON_WINDOW_S)
        self.assertEqual(run.store.labels, [])
        self.assertEqual(len(run.store.missed()), 1)
        self.assertEqual(run.motor.events, ["alarm", "tick3"])

    def test_tap_at_the_edge_of_the_window_still_labels(self):
        probe = LiveRun(constant(CLOSE, until=6), seconds=10).run()
        edge = round(self.buzz_time(probe) + BUTTON_WINDOW_S)
        run = LiveRun(constant(CLOSE, until=6), presses={edge: 0.3}, seconds=edge + 2).run()
        self.assertEqual([present for _, _, present in run.store.labels], [True])

    def test_a_buzz_can_only_be_labelled_once(self):
        run = LiveRun(constant(CLOSE, until=8), presses={7: 0.3, 9: 0.3}, seconds=12).run()
        self.assertEqual(len(run.store.labels), 1)
        self.assertEqual(len(run.store.missed()), 1)  # second tap = a new miss
        self.assertEqual(run.motor.events, ["alarm", "tick1", "tick3"])

    def test_label_goes_to_the_latest_buzz(self):
        run = LiveRun(constant(CLOSE, until=22), presses={23: 0.3}, seconds=26).run()
        buzzes = run.store.buzzes()
        self.assertEqual(len(buzzes), 2)
        self.assertEqual(run.store.labels[0][1]["event_id"], buzzes[1]["event_id"])

    def test_tp_fp_fn_in_one_session(self):
        rssi = lambda t: CLOSE if t <= 8 or 30 <= t <= 38 else None  # noqa: E731
        run = LiveRun(rssi, presses={7: 0.2, 36: 1.8, 55: 0.4}, seconds=58).run()
        self.assertEqual(len(run.store.buzzes()), 2)
        self.assertEqual([present for _, _, present in run.store.labels], [True, False])
        self.assertEqual(len(run.store.missed()), 1)
        self.assertEqual(run.motor.events, ["alarm", "tick1", "alarm", "tick2", "tick3"])


class ModeTests(unittest.TestCase):
    def test_no_db_buzzes_but_uploads_nothing_and_has_no_button(self):
        run = LiveRun(constant(CLOSE), presses={7: 0.3}, seconds=10, with_db=False).run()
        self.assertEqual(run.store.records, [])
        self.assertEqual(run.buttons_made, 0)
        self.assertEqual(run.motor.events, ["alarm"])

    def test_no_button_flag_ignores_touches(self):
        run = LiveRun(constant(CLOSE, until=8), presses={7: 0.3}, seconds=10, with_button=False).run()
        self.assertEqual(run.buttons_made, 0)
        self.assertEqual(len(run.store.buzzes()), 1)
        self.assertEqual(run.store.labels, [])

    def test_missing_credentials_still_runs_the_detector(self):
        run = LiveRun(constant(CLOSE), presses={7: 0.3}, seconds=10, store_available=False).run()
        self.assertEqual(run.buttons_made, 0)
        self.assertEqual(run.motor.events, ["alarm"])

    def test_broken_gpio_button_does_not_stop_the_detector(self):
        run = LiveRun(constant(CLOSE), seconds=10, button_fails=True).run()
        self.assertIn("Wire button off", run.output)
        self.assertEqual(len(run.store.buzzes()), 1)

    def test_no_motor_still_uploads_and_labels(self):
        run = LiveRun(constant(CLOSE, until=8), presses={7: 0.3}, seconds=10, with_motor=False).run()
        self.assertEqual(run.motor.events, [])
        self.assertEqual(len(run.store.labels), 1)

    def test_everything_is_closed_on_exit(self):
        run = LiveRun(constant(CLOSE), seconds=5).run()
        self.assertTrue(run.motor.closed)
        self.assertTrue(run.button.closed)
        self.assertTrue(run.store.closed)

    def test_ml_score_is_uploaded_when_a_model_exists(self):
        import joblib
        import numpy as np
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression().fit(
            np.array([[-80, 1, 0, 0], [-70, 2, -1, 1], [-50, 2, 0, 8], [-45, 1, 0, 9]]), [0, 0, 1, 1])
        with tempfile.TemporaryDirectory() as root:
            joblib.dump(model, Path(root) / "model.joblib")
            run = LiveRun(constant(CLOSE), seconds=6, model_dir=root).run()
        score = run.store.buzzes()[0]["ml_score"]
        self.assertIsNotNone(score)
        self.assertTrue(0.0 <= score <= 1.0)


if __name__ == "__main__":
    unittest.main()
