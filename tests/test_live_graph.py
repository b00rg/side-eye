import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from live_graph import Feed, parse  # noqa: E402

LIVE = "RSSI  -45 dBm | avg  -46.7 dBm | near streak  5.6 s | ML linger score 0.84 | ALERT"
OLD = "RSSI  -45 dBm | near streak  5.6 s | ML linger score untrained | watching"


class ParseTests(unittest.TestCase):
    def test_live_line(self):
        kind, row = parse(LIVE)
        self.assertEqual(kind, "row")
        self.assertEqual(row, dict(raw=-45, avg=-46.7, streak=5.6, score=0.84, alert=True))

    def test_older_line_without_avg_and_untrained_model(self):
        kind, row = parse(OLD)
        self.assertEqual((row["avg"], row["score"], row["alert"]), (None, None, False))

    def test_cutoff_and_notes(self):
        self.assertEqual(parse("Side Eye live: simulator packet only; threshold -55.0 dBm; motor follows ..."),
                         ("cutoff", -55.0))
        self.assertEqual(parse("Feedback (button): 6 rows saved as 'lingering'.")[0], "note")
        self.assertIsNone(parse("Waiting for simulator packet..."))


class FeedTests(unittest.TestCase):
    def test_rule_marker_uses_streak_and_cooldown(self):
        feed = Feed(window_s=120)
        for t, streak in [(0, 2.0), (1, 3.0), (2, 4.0), (16, 3.5), (17, 4.5)]:
            feed.add(f"RSSI  -45 dBm | avg  -46.0 dBm | near streak {streak:4.1f} s | ML linger score 0.10 | watching", t)
        rows, _, _ = feed.snapshot()
        self.assertEqual([r["t"] for r in rows if r["rule"]], [1, 16])   # 3 s reached at 1, then 15 s cooldown

    def test_window_drops_old_rows(self):
        feed = Feed(window_s=10)
        for t in range(30):
            feed.add(LIVE, t)
        self.assertEqual(feed.snapshot()[0][0]["t"], 19)


if __name__ == "__main__":
    unittest.main()
