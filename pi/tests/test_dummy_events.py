import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import dummy_events  # noqa: E402
import retrain  # noqa: E402
from alarm_store import COLUMNS  # noqa: E402


class DummyEventsTest(unittest.TestCase):
    def setUp(self):
        self.rows = dummy_events.dummy_rows(200)

    def test_all_marked_as_dummy(self):
        for row in self.rows:
            self.assertEqual(row["device_id"], "dummy-data")
            self.assertIn("dummy", row["note"])
            self.assertTrue(row["demo"])
            self.assertEqual(set(row), set(COLUMNS))

    def test_rows_pass_the_no_true_negatives_rule_and_are_labelled(self):
        for row in self.rows:
            self.assertTrue(row["buzzed"] or row["glasses_present"] is True)
            self.assertIsNotNone(row["glasses_present"])

    def test_all_three_outcomes_present(self):
        outcomes = {(row["buzzed"], row["glasses_present"]) for row in self.rows}
        self.assertEqual(outcomes, {(True, True), (True, False), (False, True)})

    def test_retrain_reads_them_as_both_classes(self):
        records = [(r["time"], r["glasses_present"], r["mean_rssi"], r["var_rssi"], r["slope"], r["dwell_s"])
                   for r in self.rows]
        x, y = retrain.events_to_rows(records)
        self.assertEqual(len(x[0]), 4)
        self.assertEqual(set(y), {0, 1})

    def test_same_seed_same_values(self):
        a, b = dummy_events.dummy_rows(5, seed=1), dummy_events.dummy_rows(5, seed=1)
        self.assertEqual([r["mean_rssi"] for r in a], [r["mean_rssi"] for r in b])


if __name__ == "__main__":
    unittest.main()
