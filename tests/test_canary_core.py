import unittest
from types import SimpleNamespace

from canary_core import Tracker, signature


class CanaryCoreTests(unittest.TestCase):
    def test_demo_packet_must_match_exact_payload(self):
        good = SimpleNamespace(manufacturer_data={0x058E: bytes.fromhex("CA01")}, service_uuids=[])
        other_meta = SimpleNamespace(manufacturer_data={0x058E: b"OTHER"}, service_uuids=[])
        self.assertEqual(signature(good), "phone_simulator")
        self.assertIsNone(signature(other_meta))
        self.assertEqual(signature(other_meta, demo_only=False), "company_0x058e")

    def test_close_streak_requires_fresh_continuous_observations(self):
        tracker = Tracker()
        for i in range(8):
            tracker.add(float(i) * 0.5, -50)
        self.assertGreaterEqual(tracker.snapshot(3.5, -60).close_streak_s, 3)
        self.assertIsNone(tracker.snapshot(6.0, -60))
        tracker.add(6.1, -80)
        self.assertEqual(tracker.snapshot(6.1, -60).close_streak_s, 0)
        for i in range(1, 5):
            tracker.add(6.1 + i * 0.3, -80)
        self.assertEqual(tracker.snapshot(7.3, -60).close_streak_s, 0)

    def test_long_gap_resets_old_signal(self):
        tracker = Tracker()
        for i in range(6):
            tracker.add(i * 0.5, -45)
        tracker.add(20, -85)
        self.assertEqual(len(tracker.samples), 1)
        self.assertEqual(tracker.samples[0].smooth, -85)


if __name__ == "__main__":
    unittest.main()
