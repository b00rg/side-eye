import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import train_model
from feedback import UNCONFIRMED_WEIGHT, Feedback, FeedbackStore, load_rows


def rows(store):
    return store.db.execute("SELECT source, label, weight FROM feedback_rows ORDER BY id").fetchall()


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = FeedbackStore(Path(self.dir.name) / "feedback.db")
        self.fb = Feedback(self.store)

    def tearDown(self):
        self.store.db.close()
        self.dir.cleanup()

    def feed(self, start, end, address="aa", rssi=-50):
        for t in range(start, end):
            self.fb.observe(float(t), address, [rssi, 2.0, 0.0, 5.0])

    def test_press_labels_last_window_of_nearest_device_as_lingering(self):
        self.feed(0, 30, "far", rssi=-80)
        self.feed(0, 30, "near", rssi=-45)
        added = self.fb.press(29.0)
        self.assertEqual(added, 11)                       # t = 19..29, the last 10 s
        self.assertEqual({r for r in rows(self.store)}, {("button", 1, 1.0)})
        stored = self.store.db.execute("SELECT DISTINCT mean_rssi FROM feedback_rows").fetchall()
        self.assertEqual(stored, [(-45.0,)])              # the nearest device, not the far one

    def test_confirmed_alert_adds_no_negative(self):
        self.feed(0, 20)
        self.fb.alert(19.0, "aa")
        self.feed(20, 25)
        self.fb.press(24.0)                               # within 30 s of the alert
        self.assertEqual(self.fb.tick(100.0), 0)
        self.assertTrue(all(label == 1 for _, label, _ in rows(self.store)))

    def test_press_just_before_alert_confirms_it(self):
        self.feed(0, 20)
        self.fb.press(17.0)                               # wearer presses, then the buzz comes
        self.fb.alert(19.0, "aa")
        self.assertEqual(self.fb.tick(100.0), 0)
        self.assertTrue(all(label == 1 for _, label, _ in rows(self.store)))

    def test_unconfirmed_alert_becomes_weak_negative(self):
        self.feed(0, 20)
        self.fb.alert(19.0, "aa")
        self.assertEqual(self.fb.tick(40.0), 0)           # still waiting (21 s)
        self.assertGreater(self.fb.tick(50.0), 0)         # 31 s, no press
        self.assertEqual({r for r in rows(self.store)}, {("unconfirmed_alert", 0, UNCONFIRMED_WEIGHT)})

    def test_no_device_identifiers_are_stored(self):
        cols = [c[1] for c in self.store.db.execute("PRAGMA table_info(feedback_rows)")]
        self.assertFalse({"address", "mac", "device", "rssi_trace"} & set(cols))

    def test_press_with_nothing_nearby_adds_nothing(self):
        self.assertEqual(self.fb.press(5.0), 0)


class TrainWithFeedbackTests(unittest.TestCase):
    def test_feedback_rows_are_used_in_training(self):
        original_root = train_model.ROOT
        try:
            with tempfile.TemporaryDirectory() as directory:
                train_model.ROOT = Path(directory)
                run_dir = train_model.ROOT / "data" / "runs"
                run_dir.mkdir(parents=True)
                header = "run_id,scenario,t,mean_rssi,var_rssi,slope,dwell_s,close_streak_s,packet_rate_hz,label\n"
                for label in (0, 1):
                    for n in range(4):
                        lines = [f"{label}-{n},x,{s},{-55 if label else -85},1,0,{s if label else 0},0,5,{label}\n" for s in range(8)]
                        (run_dir / f"{label}-{n}.csv").write_text(header + "".join(lines))
                store = FeedbackStore(train_model.ROOT / "feedback.db")
                store.add([[-50, 2, 0, 8]] * 5, 1, 1.0, "button")
                store.add([[-70, 2, 0, 1]] * 5, 0, 0.3, "unconfirmed_alert")
                store.db.close()
                self.assertEqual(len(load_rows(train_model.ROOT / "feedback.db")[0]), 10)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    train_model.main(["--feedback", str(train_model.ROOT / "feedback.db")])
                self.assertIn("Plus 10 feedback rows from the button (5 'lingering')", out.getvalue())
                self.assertTrue((train_model.ROOT / "model.joblib").is_file())
        finally:
            train_model.ROOT = original_root


if __name__ == "__main__":
    unittest.main()
