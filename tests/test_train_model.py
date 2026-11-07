import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
import train_model


class TrainingSmokeTest(unittest.TestCase):
    def test_early_rule_alert_uses_cooldown_in_run_metrics(self):
        class NeverPositiveModel:
            def predict_proba(self, x):
                return np.array([[1.0, 0.0]] * len(x))

        rows = [
            {"run_id": "one", "scenario": "linger", "t": str(second),
             "mean_rssi": "-50", "var_rssi": "1", "slope": "0", "dwell_s": str(second),
             "close_streak_s": "3" if second >= 3 else "0",
             "label": "1" if 10 <= second <= 15 else "0"}
            for second in range(21)
        ]
        result = train_model.event_metrics([("one", rows)], NeverPositiveModel())
        self.assertEqual(result["rule_premature"], 1)
        self.assertEqual(result["rule_hit"], 0)

    def test_training_uses_independent_runs_and_writes_model(self):
        original_root = train_model.ROOT
        try:
            with tempfile.TemporaryDirectory() as directory:
                train_model.ROOT = Path(directory)
                run_dir = train_model.ROOT / "data" / "runs"
                run_dir.mkdir(parents=True)
                for label in (0, 1):
                    for run_number in range(4):
                        run_id = f"{label}-{run_number}"
                        with (run_dir / f"{run_id}.csv").open("w", newline="") as file:
                            writer = csv.DictWriter(file, fieldnames=[
                                "run_id", "scenario", "t", "mean_rssi", "var_rssi", "slope",
                                "dwell_s", "close_streak_s", "packet_rate_hz", "label"
                            ])
                            writer.writeheader()
                            for second in range(8):
                                writer.writerow({
                                    "run_id": run_id,
                                    "scenario": "linger" if label else "far",
                                    "t": second,
                                    "mean_rssi": -55 if label else -85,
                                    "var_rssi": 1,
                                    "slope": 0,
                                    "dwell_s": second if label else 0,
                                    "close_streak_s": second if label else 0,
                                    "packet_rate_hz": 5,
                                    "label": label,
                                })
                folds = train_model.balanced_group_folds(train_model.load_runs())
                self.assertEqual(len(folds), 4)
                for fold in folds:
                    self.assertEqual(len(fold), 2)
                with contextlib.redirect_stdout(io.StringIO()):
                    train_model.main()
                self.assertTrue((train_model.ROOT / "model.joblib").is_file())
        finally:
            train_model.ROOT = original_root


if __name__ == "__main__":
    unittest.main()
