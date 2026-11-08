import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

import train_model
from canary_core import ModelAlert, project_root


class ModelAlertTests(unittest.TestCase):
    def test_needs_two_high_scores_close_together(self):
        alert = ModelAlert()
        self.assertFalse(alert.update(0.0, 0.9))   # first high score alone never fires
        self.assertTrue(alert.update(2.0, 0.85))   # second within 2.5 s fires
        self.assertFalse(alert.update(10.0, 0.95))  # 8 s later: counts as a new first
        self.assertFalse(alert.update(11.0, 0.5))  # low score never fires

    def test_gap_too_long_does_not_fire(self):
        alert = ModelAlert()
        alert.update(0.0, 0.9)
        self.assertFalse(alert.update(3.0, 0.9))


class ProjectRootTests(unittest.TestCase):
    def test_repo_layout_and_flat_layout(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            (repo / "pi").mkdir(parents=True)
            (repo / "nist").mkdir()
            flat = Path(d) / "canary"
            flat.mkdir()
            self.assertEqual(project_root(repo / "pi" / "canary.py"), repo.resolve())
            self.assertEqual(project_root(flat / "canary.py"), flat.resolve())

    def test_cloud_image_layout_without_nist(self):
        """gcp/Dockerfile copies only pi/ and data/: the walks must still be found under /app/data."""
        with tempfile.TemporaryDirectory() as d:
            app = Path(d) / "app"
            (app / "pi").mkdir(parents=True)
            (app / "data" / "runs_session1").mkdir(parents=True)
            self.assertEqual(project_root(app / "pi" / "retrain_shape.py"), app.resolve())

    def test_flat_folder_with_its_own_data_stays_flat(self):
        with tempfile.TemporaryDirectory() as d:
            flat = Path(d) / "canary"
            (flat / "data").mkdir(parents=True)
            self.assertEqual(project_root(flat / "canary.py"), flat.resolve())


class Model3TrainingTests(unittest.TestCase):
    def test_sim_rows_train_and_save_model(self):
        original_root = train_model.ROOT
        try:
            with tempfile.TemporaryDirectory() as directory:
                train_model.ROOT = Path(directory)
                run_dir = train_model.ROOT / "data" / "runs"
                run_dir.mkdir(parents=True)
                fields = ["run_id", "scenario", "t", "mean_rssi", "var_rssi", "slope", "dwell_s",
                          "close_streak_s", "packet_rate_hz", "label"]
                for label in (0, 1):
                    for n in range(4):
                        with (run_dir / f"{label}-{n}.csv").open("w", newline="") as f:
                            w = csv.DictWriter(f, fieldnames=fields)
                            w.writeheader()
                            for s in range(8):
                                w.writerow(dict(run_id=f"{label}-{n}", scenario="linger" if label else "far", t=s,
                                                mean_rssi=-55 if label else -85, var_rssi=1, slope=0,
                                                dwell_s=s if label else 0, close_streak_s=s if label else 0,
                                                packet_rate_hz=5, label=label))
                sim = train_model.ROOT / "sim_rows.csv"
                with sim.open("w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["mean_rssi", "var_rssi", "slope", "dwell_s", "label"])
                    for i in range(40):
                        w.writerow([-50 if i % 2 else -80, 2, 0, 8 if i % 2 else 0, i % 2])
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    train_model.main(["--sim-rows", "sim_rows.csv"])
                self.assertIn("Model 3: real walks + 40 simulated rows", out.getvalue())
                self.assertTrue((train_model.ROOT / "model.joblib").is_file())
        finally:
            train_model.ROOT = original_root


if __name__ == "__main__":
    unittest.main()
