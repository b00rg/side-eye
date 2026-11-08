"""retrain_shape.py: walks + labelled Tiger events -> shape_model.joblib, judged on held-out walks."""
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import joblib
import retrain_shape
import train_model
from alarm_store import SHAPE_COLUMNS
from shape_core import SHAPE


def write_walks(folder, n=4):
    """n lingers (flat, then a rise that holds) and n walk-pasts (flat throughout), one row per second."""
    folder.mkdir(parents=True)
    for kind in ("linger", "passer"):
        for i in range(n):
            with (folder / f"{kind}-{i}.csv").open("w", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["run_id", "scenario", "t", "mean_rssi", "label"])
                writer.writeheader()
                for t in range(30):
                    stopped = kind == "linger" and t >= 12
                    writer.writerow({"run_id": f"{kind}-{i}", "scenario": kind, "t": t,
                                     "mean_rssi": -50 if stopped else -62 + (i % 2),
                                     "label": int(stopped and t >= 15)})


# (time, glasses_present, rise, drop, steady, held)
EVENTS = [(None, True, 9.0, 0.0, 0.5, 6.0), (None, False, 5.0, 2.0, 3.0, 1.0), (None, True, 11.0, 0.0, 0.2, 9.0)]


class RetrainShapeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.patches = [mock.patch.object(train_model, "ROOT", self.root),
                        mock.patch("alarm_store.service_url", return_value="postgres://fake"),
                        mock.patch.object(retrain_shape, "fetch_events",
                                          return_value=retrain_shape.events_to_rows(EVENTS))]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.directory.cleanup()

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = retrain_shape.main(list(argv))
        return code, out.getvalue()

    def test_tiger_columns_are_the_shape_inputs_in_order(self):
        self.assertEqual(tuple(SHAPE_COLUMNS), tuple(SHAPE))
        sql = retrain_shape.EVENTS_SQL.format(demo_filter="")
        self.assertIn("rise, drop, steady, held FROM events", sql)
        self.assertIn("glasses_present IS NOT NULL", sql)
        for name in SHAPE:
            self.assertIn(f"AND {name} IS NOT NULL", sql)   # rows from canary.py live (no shape) are skipped
        self.assertNotIn("glasses_ref", sql)

    def test_events_become_labelled_rows(self):
        x, y = retrain_shape.events_to_rows(EVENTS)
        self.assertEqual(y, [1, 0, 1])
        self.assertEqual(x[1], [5.0, 2.0, 3.0, 1.0])

    def test_walk_features_match_the_live_tracker(self):
        write_walks(self.root / "data" / "runs_session1")
        walks = retrain_shape.load_walks([self.root / "data" / "runs_session1"])
        w = walks["runs_session1/linger-0"]
        self.assertEqual(len(w["x"]), 30)
        self.assertEqual(w["x"][12][0], 12.0)          # rise: -50 minus the recent low of -62
        self.assertEqual(sum(w["y"]), 15)

    def test_writes_shape_model_and_metadata(self):
        write_walks(self.root / "data" / "runs_session1")
        write_walks(self.root / "data" / "runs_session2")
        code, out = self.run_main("--tiger-weight", "0.5")
        self.assertEqual(code, 0, out)
        model = joblib.load(self.root / "shape_model.joblib")
        self.assertEqual(tuple(model.feature_names_shape), tuple(SHAPE))
        self.assertEqual(model.n_features_in_, 4)
        info = json.loads((self.root / "models" / "shape_latest.json").read_text())
        self.assertEqual(info["walks"], 16)
        self.assertEqual(info["tiger_events"], 3)
        self.assertIn("walks + Tiger", info["cv"])
        self.assertFalse((self.root / "model.joblib").exists())   # never touches the level model

    def test_keeps_current_model_if_tiger_rows_make_held_out_walks_worse(self):
        write_walks(self.root / "data" / "runs_session1")
        worse = {"walks only": {"ndcf": 0.2}, "walks + Tiger": {"ndcf": 0.5}}
        with mock.patch.object(retrain_shape, "cross_validate", return_value=worse):
            code, out = self.run_main()
            self.assertEqual(code, 0)
            self.assertIn("keeping the current model", out)
            self.assertFalse((self.root / "shape_model.joblib").exists())
            code, out = self.run_main("--force")
        self.assertTrue((self.root / "shape_model.joblib").exists())

    def test_too_few_events_keeps_current_model(self):
        write_walks(self.root / "data" / "runs_session1")
        code, out = self.run_main("--min-events", "10")
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "shape_model.joblib").exists())

    def test_dry_run_writes_nothing(self):
        write_walks(self.root / "data" / "runs_session1")
        self.run_main("--dry-run")
        self.assertFalse((self.root / "shape_model.joblib").exists())

    def test_no_walks_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.run_main()

    def test_scoring_matches_shape_confirm_rules(self):
        class Fixed:
            def __init__(self, scores):
                self.scores = scores

            def predict_proba(self, x):
                import numpy as np
                return np.array([[1 - s, s] for s in self.scores])

        linger = {"t": [0, 1, 2, 3], "x": [[0] * 4] * 4, "y": [0, 0, 1, 1]}
        early = retrain_shape.score_walks([("a", linger)], Fixed([0.9, 0.9, 0.9, 0.9]))
        self.assertEqual((early["hit"], early["early"]), (0, 1))     # alert at 1 s, stop labelled from 2 s
        on_time = retrain_shape.score_walks([("a", linger)], Fixed([0.1, 0.1, 0.9, 0.9]))
        self.assertEqual((on_time["hit"], on_time["ndcf"]), (1, 0.0))
        passer = {"t": [0, 1, 2], "x": [[0] * 4] * 3, "y": [0, 0, 0]}
        fa = retrain_shape.score_walks([("p", passer)], Fixed([0.9, 0.9, 0.1]))
        self.assertEqual(fa["false_alarm"], 1)


if __name__ == "__main__":
    unittest.main()
