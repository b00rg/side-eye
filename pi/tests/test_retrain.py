import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import joblib
import retrain
import train_model


def write_walks(run_dir):
    run_dir.mkdir(parents=True)
    for label in (0, 1):
        for run_number in range(4):
            run_id = f"{label}-{run_number}"
            with (run_dir / f"{run_id}.csv").open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=[
                    "run_id", "scenario", "t", "mean_rssi", "var_rssi", "slope",
                    "dwell_s", "close_streak_s", "packet_rate_hz", "label"])
                writer.writeheader()
                for second in range(8):
                    writer.writerow({
                        "run_id": run_id, "scenario": "linger" if label else "far", "t": second,
                        "mean_rssi": -55 if label else -85, "var_rssi": 1, "slope": 0,
                        "dwell_s": second if label else 0, "close_streak_s": second if label else 0,
                        "packet_rate_hz": 5, "label": label})


# (time, glasses_present, mean_rssi, var_rssi, slope, dwell_s)
EVENTS = [(None, True, -58.0, 2.0, 0.1, 6.0), (None, False, -66.0, 9.0, 1.5, 3.0),
          (None, True, -60.0, 1.0, 0.0, 8.0)]


class RetrainTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.patches = [mock.patch.object(train_model, "ROOT", self.root),
                        mock.patch("alarm_store.service_url", return_value="postgres://fake"),
                        mock.patch.object(retrain, "fetch_events",
                                          return_value=retrain.events_to_rows(EVENTS))]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.directory.cleanup()

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = retrain.main(list(argv))
        return code, out.getvalue()

    def test_events_become_labelled_rows(self):
        x, y = retrain.events_to_rows(EVENTS)
        self.assertEqual(y, [1, 0, 1])
        self.assertEqual(x[1], [-66.0, 9.0, 1.5, 3.0])

    def test_sql_only_reads_labelled_rows_with_features(self):
        sql = retrain.EVENTS_SQL.format(demo_filter="AND NOT demo ")
        self.assertIn("glasses_present IS NOT NULL", sql)
        self.assertIn("AND NOT demo", sql)
        self.assertNotIn("glasses_ref", sql)

    def test_walks_plus_tiger_writes_model_and_metadata(self):
        write_walks(self.root / "data" / "runs")
        code, out = self.run_main("--tiger-weight", "0.5")
        self.assertEqual(code, 0)
        self.assertIn("walks + Tiger", out)
        model = joblib.load(self.root / "model.joblib")
        self.assertEqual(model.n_features_in_, 4)
        info = json.loads((self.root / "models" / "latest.json").read_text())
        self.assertEqual(info["tiger_events"], 3)
        self.assertEqual(info["walks"], 8)
        self.assertTrue((self.root / "models" / info["model"]).is_file())

    def test_too_few_events_keeps_current_model(self):
        code, out = self.run_main("--min-events", "10")
        self.assertEqual(code, 0)
        self.assertIn("keeping the current model", out)
        self.assertFalse((self.root / "model.joblib").exists())

    def test_tiger_only_trains_without_walks(self):
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("no held-out evaluation", out)
        self.assertTrue((self.root / "model.joblib").is_file())

    def test_out_dir_receives_model(self):
        write_walks(self.root / "data" / "runs")
        out = self.root / "bucket"
        code, _ = self.run_main("--out-dir", str(out))
        self.assertEqual(code, 0)
        self.assertTrue((out / "model.joblib").is_file())
        self.assertTrue((out / "models" / "latest.json").is_file())
        self.assertFalse((self.root / "model.joblib").exists())

    def test_dry_run_writes_nothing(self):
        write_walks(self.root / "data" / "runs")
        self.run_main("--dry-run")
        self.assertFalse((self.root / "model.joblib").exists())


if __name__ == "__main__":
    unittest.main()
