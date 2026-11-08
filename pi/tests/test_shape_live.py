"""End-to-end tests of shape_live.py with simulated hardware (reuses test_live_loop's fakes).

The real shape_live main() loop runs; the clock, scanner, motor, wire button and Tiger store are faked.
The model is a small forest trained here to score high once the signal has risen and held.
"""

import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import alarm_store
import canary
import joblib
import shape_live
from canary_core import BUTTON_WINDOW_S
from test_live_loop import CLOSE, FAR, THRESHOLD, FakeScanner, LiveRun, StopLive
from train_model import model_factory


def rise_then_hold(start=8):
    return lambda t: FAR if t < start else CLOSE


def flat(t):
    return FAR


def write_model(folder):
    """Risen-and-held rows -> 1, everything else -> 0 (a stand-in for the real shape model)."""
    x, y = [], []
    for rise in range(0, 30, 2):
        for held in range(0, 15):
            for drop in (0.0, 5.0):
                x.append([float(rise), drop, 2.0, float(held)])
                y.append(int(rise >= 4 and held >= 2 and drop == 0.0))
    model = model_factory().fit(x, y)
    model.feature_names_shape = ("rise", "drop", "steady", "held")
    joblib.dump(model, Path(folder) / "shape_model.joblib")


class ShapeRun(LiveRun):
    def __init__(self, rssi_at, presses=None, seconds=30, no_db=False, no_button=False, **kw):
        super().__init__(rssi_at, presses, seconds, **kw)
        self.args = SimpleNamespace(no_motor=False, no_db=no_db, no_button=no_button, alert=0.8, cooldown=15.0,
                                    pulses=3, on=0.4, off=0.15)
        self.buzzes = []

    async def _buzz(self, motor, pulses, on_s, off_s):
        motor.events.append("alarm")
        self.buzzes.append(round(self.now - 1000.0))

    def run(self):
        root = tempfile.mkdtemp()
        write_model(root)
        patches = [
            mock.patch.object(shape_live, "asyncio", SimpleNamespace(sleep=self._sleep)),
            mock.patch.object(shape_live, "time", SimpleNamespace(monotonic=lambda: self.now)),
            mock.patch.object(shape_live, "Session", self._make_session),
            mock.patch.object(shape_live, "new_scanner", lambda callback: FakeScanner()),
            mock.patch.object(shape_live, "threshold", lambda: THRESHOLD),
            mock.patch.object(shape_live, "Motor", lambda: self.motor),
            mock.patch.object(shape_live, "buzz", self._buzz),
            mock.patch.object(shape_live, "project_root", lambda _: Path(root)),
            mock.patch.object(canary, "WireButton", self._make_button),
            mock.patch.object(alarm_store.AlarmStore, "from_env", lambda: self.store),
        ]
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            try:
                asyncio.run(shape_live.main(self.args))
            except StopLive:
                pass
        self.output = out.getvalue()
        return self


class ShapeLiveTests(unittest.TestCase):
    def test_rise_that_holds_buzzes_and_uploads_shape_inputs(self):
        run = ShapeRun(rise_then_hold(), seconds=20).run()
        self.assertEqual(len(run.store.buzzes()), 1, run.output)
        _, alarm = run.store.records[0]
        self.assertEqual(alarm["rule"], "shape-model")
        self.assertGreaterEqual(alarm["rise"], 4.0)
        self.assertGreaterEqual(alarm["held"], 1.0)
        self.assertGreaterEqual(alarm["ml_score"], 0.8)
        self.assertIn("alarm", run.motor.events)

    def test_flat_signal_never_buzzes(self):
        run = ShapeRun(flat, seconds=20).run()
        self.assertEqual(run.store.buzzes(), [])
        self.assertNotIn("alarm", run.motor.events)

    def test_touch_after_buzz_labels_it_glasses_there(self):
        first = ShapeRun(rise_then_hold(), seconds=20).run().buzzes[0]
        run = ShapeRun(rise_then_hold(), presses={first + 2: 0.2}, seconds=first + 4).run()
        [(_, alarm, present)] = run.store.labels
        self.assertTrue(present)
        self.assertEqual(alarm["rule"], "shape-model")
        self.assertIn("tick1", run.motor.events)

    def test_hold_after_buzz_labels_false_alarm(self):
        first = ShapeRun(rise_then_hold(), seconds=20).run().buzzes[0]
        run = ShapeRun(rise_then_hold(), presses={first + 2: 2.0}, seconds=first + 4).run()
        [(_, _, present)] = run.store.labels
        self.assertFalse(present)

    def test_touch_without_buzz_reports_a_miss_with_shape_inputs(self):
        run = ShapeRun(flat, presses={10: 0.2}, seconds=12).run()
        [missed] = [row for _, row in run.store.records if not row["buzzed"]]
        self.assertTrue(missed["glasses_present"])
        for key in ("rise", "drop", "steady", "held"):
            self.assertIsNotNone(missed[key], key)
        self.assertGreater(BUTTON_WINDOW_S, 0)

    def test_no_db_means_no_uploads_and_no_button(self):
        run = ShapeRun(rise_then_hold(), seconds=20, no_db=True).run()
        self.assertEqual(run.store.records, [])
        self.assertEqual(run.buttons_made, 0)
        self.assertIn("alarm", run.motor.events)   # still buzzes

    def test_runs_without_tiger_credentials(self):
        run = ShapeRun(rise_then_hold(), seconds=20, store_available=False).run()
        self.assertEqual(run.buttons_made, 0)
        self.assertIn("alarm", run.motor.events)


if __name__ == "__main__":
    unittest.main()
