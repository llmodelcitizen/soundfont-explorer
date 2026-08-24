"""render/cloud/planner.py: shard planning + estimate (shared by submit.py and the admin)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cloud"))
import planner  # noqa: E402


class PlanShardsTests(unittest.TestCase):
    def test_longest_first_balances(self):
        d = {"a": 600, "b": 500, "c": 100, "d": 90, "e": 10}
        shards = planner.plan_shards(["a", "b", "c", "d", "e"], 2, d)
        self.assertEqual(len(shards), 2)
        totals = sorted(s["duration_total_s"] for s in shards)
        self.assertEqual(totals, [610, 690])          # a+d | b+c+e (greedy, ties to first)
        self.assertEqual(shards[0]["songs"][0], "a")  # longest dealt first

    def test_more_shards_than_songs(self):
        shards = planner.plan_shards(["a"], 8, {"a": 100})
        self.assertEqual(len(shards), 1)
        self.assertEqual(shards[0]["songs"], ["a"])

    def test_unknown_duration_defaults(self):
        shards = planner.plan_shards(["x"], 1, {})
        self.assertEqual(shards[0]["duration_total_s"], planner.DEFAULT_DURATION_S)

    def test_estimate(self):
        shards = [{"songs": ["a", "b"], "duration_total_s": 1}]
        cpu_h, usd = planner.estimate(shards, 566)
        self.assertAlmostEqual(cpu_h, 2 * 566 * 53.0 / 3600)
        self.assertAlmostEqual(usd, cpu_h * 0.019)


if __name__ == "__main__":
    unittest.main()
