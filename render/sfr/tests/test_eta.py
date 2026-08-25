"""render/cloud/eta.py: the four different answers a fleet run has to report (#25).

The scenario throughout is the 2026-08-25 run: 8 shards, only 2 able to run at once, children
taking ~25 minutes of which ~13 is the publish tail.
"""
import os
import sys
import unittest

CLOUD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cloud")
eta = None   # bound by setUpModule


def setUpModule():
    """eta.py lives in render/cloud, not render/sfr (see test_planner.ImportHygieneTests)."""
    global eta
    sys.path.insert(0, CLOUD)
    import eta as _eta
    eta = _eta


def tearDownModule():
    if CLOUD in sys.path:
        sys.path.remove(CLOUD)


M = 60.0


def fc(**kw):
    base = dict(total_shards=8, concurrent=2, finished=2, running=2,
                child_durations_s=[24 * M, 28 * M], publish_tail_s=[13 * M, 15 * M],
                running_elapsed_s=[10 * M, 20 * M])
    base.update(kw)
    return eta.RunForecast(**base)


class ForecastTests(unittest.TestCase):
    def test_the_run_that_prompted_the_issue(self):
        f = fc()
        self.assertEqual(f.pending, 4)
        self.assertEqual(f.waves_remaining, 2)              # 4 pending, 2 at a time
        self.assertEqual(f.typical_child_s, 26 * M)
        self.assertEqual(f.typical_tail_s, 14 * M)
        # the youngest running child has 26 - 10 = 16 minutes left, and stops rendering 14 before that
        self.assertAlmostEqual(f.current_child_s(), 16 * M)
        self.assertAlmostEqual(f.current_render_s(), 2 * M)
        # fleet zero is NOT the current child: two more waves of ~26m each, then scale-in
        self.assertAlmostEqual(f.fleet_zero_s(), 16 * M + 2 * 26 * M + eta.SCALE_IN_LAG_S)

    def test_a_phase_eta_is_never_the_run_eta(self):
        f = fc()
        self.assertGreater(f.fleet_zero_s(), f.current_child_s() * 4)

    def test_full_concurrency_has_no_extra_waves(self):
        f = fc(concurrent=8, finished=0, running=8, running_elapsed_s=[5 * M] * 8)
        self.assertEqual(f.pending, 0)
        self.assertEqual(f.waves_remaining, 0)
        self.assertAlmostEqual(f.fleet_zero_s(), 21 * M + eta.SCALE_IN_LAG_S)
        self.assertNotIn("scheduling wave", " ".join(f.lines()))

    def test_nothing_observed_yet_means_no_guess(self):
        f = fc(finished=0, child_durations_s=[], publish_tail_s=[], running_elapsed_s=[2 * M])
        self.assertIsNone(f.typical_child_s)
        self.assertIsNone(f.current_child_s())
        self.assertIsNone(f.current_render_s())
        self.assertIsNone(f.fleet_zero_s())
        self.assertIn("unknown", " ".join(f.lines()))

    def test_unknown_concurrency_hides_the_wave_maths_but_not_the_child(self):
        f = fc(concurrent=None)
        self.assertIsNone(f.waves_remaining)
        self.assertIsNone(f.fleet_zero_s())
        self.assertAlmostEqual(f.current_child_s(), 16 * M)

    def test_an_overdue_child_reports_zero_not_negative(self):
        f = fc(running_elapsed_s=[40 * M])
        self.assertEqual(f.current_child_s(), 0.0)
        self.assertEqual(f.current_render_s(), 0.0)

    def test_lines_name_all_four_answers(self):
        text = " ".join(fc().lines())
        for phrase in ("shards done", "current child", "scheduling wave", "fleet at zero"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
