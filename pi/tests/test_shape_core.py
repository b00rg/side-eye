import os
import sys
import unittest

from shape_core import ShapeTracker

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


class ShapeCoreTests(unittest.TestCase):
    def test_rise_then_hold(self):
        tr = ShapeTracker()
        rows = [tr.update(v) for v in [-60] * 5 + [-50] * 5]
        self.assertEqual(rows[4], [0.0, 0.0, 0.0, 0.0])
        rise, drop, _steady, held = rows[-1]
        self.assertEqual((rise, drop, held), (10.0, 0.0, 5.0))

    def test_rise_then_fall_resets_held(self):
        tr = ShapeTracker()
        for v in [-60] * 3 + [-50] * 3:
            tr.update(v)
        rise, drop, _steady, held = tr.update(-59)
        self.assertEqual((rise, drop, held), (1.0, 9.0, 0.0))

    @unittest.skipUnless(os.path.isdir(os.path.join(REPO, "data", "runs_session1")), "repo data not available")
    def test_matches_offline_features_on_real_walks(self):
        """Live features must equal the ones the confirmatory test used (nist/shape_features.py)."""
        try:
            import pandas as pd
        except ImportError:
            self.skipTest("pandas not installed (Pi)")
        sys.path.insert(0, os.path.join(REPO, "nist"))
        from compare_models import load_real
        from shape_features import SHAPE, add_shape
        for folder in ("runs_session1", "runs_session2"):
            path = os.path.join(REPO, "data", folder)
            if not os.path.isdir(path):
                continue
            for _, walk in load_real(path).groupby("walk", sort=False):
                offline = add_shape(walk)
                tr = ShapeTracker()
                live = pd.DataFrame([tr.update(v) for v in offline.mean_rssi], columns=SHAPE, index=offline.index)
                pd.testing.assert_frame_equal(live, offline[SHAPE].astype(float), atol=1e-9)


if __name__ == "__main__":
    unittest.main()
