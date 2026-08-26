"""render/cloud/planner.py: shard planning + estimate (shared by submit.py and the admin)."""
import ast
import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

CLOUD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cloud")
planner = None   # bound by setUpModule


def setUpModule():
    """planner.py lives in render/cloud, not render/sfr, so this module has to reach for
    it. Scoped to the module rather than done at import time: a bare insert runs during
    DISCOVERY of the whole render/sfr suite and is never undone, putting render/cloud ahead
    of everything on sys.path for every other test in the run (#19)."""
    global planner
    sys.path.insert(0, CLOUD)
    import planner as _planner
    planner = _planner


def tearDownModule():
    if CLOUD in sys.path:
        sys.path.remove(CLOUD)


def variant(vid, engine, completeness="full_gm", **kw):
    v = {"id": vid, "engine": engine, "publish": True, "requires_rom": False,
         "facets": {"completeness": completeness}}
    v.update(kw)
    return v


def write_repo(root: pathlib.Path, variants, engines) -> None:
    (root / "catalog").mkdir(parents=True)
    (root / "render").mkdir(parents=True)
    (root / "catalog" / "variants.json").write_text(json.dumps({"schema": 1, "variants": variants}))
    (root / "render" / "engines.json").write_text(json.dumps({"engines": engines}))


class VariantCountTests(unittest.TestCase):
    """The per-song job count is read from the catalog, not a literal (#19)."""

    def setUp(self):
        self.repo = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)

    def test_counts_follow_the_fleet_selection(self):
        write_repo(self.repo, [
            variant("sf2-a", "fluidsynth"),
            variant("sf2-b", "fluidsynth", "partial"),
            variant("sf2-c", "fluidsynth", "single_instrument"),      # not in the default classes
            variant("sf2-d", "fluidsynth", publish=False),             # unpublished
            variant("sf2-e", "fluidsynth", alias_of="sf2-a"),          # byte-identical twin
            variant("adl-b0", "adlmidi"),
            variant("adl-b1", "adlmidi", "melodic_only"),
            variant("sc55-x", "sc55", requires_rom=True),              # no ROMs on the fleet
            variant("opn-x", "opnmidi"),                               # engine not pinned
        ], {"fluidsynth": {"version": "2.3"}, "adlmidi": {"version": "1.6"},
            "sc55": {"version": "1"}, "opnmidi": {}})
        self.assertEqual(planner.variant_counts(self.repo), {"fluidsynth": 2, "adlmidi": 2})

    def test_real_catalog_matches_sfr_plan_rules(self):
        # the repo's own catalog: every counted variant is published, ROM-free, pinned
        repo = pathlib.Path(__file__).resolve().parents[3]
        counts = planner.variant_counts(repo)
        self.assertGreater(sum(counts.values()), 0)
        self.assertNotIn("sc55", counts)
        self.assertNotIn("munt", counts)

    def test_per_song_engines_and_limit(self):
        counts = {"fluidsynth": 400, "adlmidi": 100, "opnmidi": 10}
        self.assertEqual(planner.variants_per_song(counts), 510)
        self.assertEqual(planner.variants_per_song(counts, ["adlmidi"]), 100)
        self.assertEqual(planner.variants_per_song(counts, ["adlmidi", "opnmidi", "adlmidi"]), 110)
        self.assertEqual(planner.variants_per_song(counts, ["adlmidi"], limit=18), 18)
        self.assertEqual(planner.variants_per_song(counts, None, limit=9999), 510)
        with self.assertRaises(ValueError):
            planner.variants_per_song(counts, ["adlmid"])


class ImportHygieneTests(unittest.TestCase):
    """planner.py is not in render/sfr, and reaching for it must not change sys.path for
    the rest of the suite that runs beside this module (#19)."""

    def test_sys_path_is_not_touched_at_module_level(self):
        tree = ast.parse(pathlib.Path(__file__).read_text())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                continue     # setUpModule/tearDownModule are where it belongs
            bad = [c for c in ast.walk(node)
                   if isinstance(c, ast.Call) and ast.unparse(c.func).startswith("sys.path")]
            self.assertEqual(bad, [], "a module-level insert runs during discovery")

    def test_teardown_puts_sys_path_back(self):
        self.assertIn(CLOUD, sys.path)          # while this module's tests run
        tearDownModule()
        self.assertNotIn(CLOUD, sys.path)
        setUpModule()                           # leave it as the other tests here expect
        self.assertIn(CLOUD, sys.path)


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
        shards = [{"songs": ["a", "b"], "duration_total_s": 300}]
        cpu_h, usd = planner.estimate(shards, 566)
        self.assertAlmostEqual(cpu_h, 2 * 566 * planner.job_cost_vcpu_s(150) / 3600)
        self.assertAlmostEqual(usd, cpu_h * planner.USD_PER_VCPU_HOUR)


class CostModelTests(unittest.TestCase):
    """Cost is overhead + DSP, not a flat per-job constant (measured on the two 2026-08-25
    fleet runs; the old flat 53 vCPU-s overestimated them by 2.4x and 3.5x)."""

    def test_the_two_runs_it_was_fitted_to(self):
        for mean_s, per_job in ((237, 34.5), (90, 18.8)):
            self.assertAlmostEqual(planner.job_cost_vcpu_s(mean_s), per_job, delta=0.15)

    def test_a_longer_song_costs_more_per_job(self):
        self.assertGreater(planner.job_cost_vcpu_s(240), planner.job_cost_vcpu_s(60))

    def test_the_overhead_term_dominates_a_short_song(self):
        """Half a 90 s song's job cost is process spawn, which is why the fleet ran at 37-60%
        CPU with every worker busy (#45)."""
        self.assertGreater(planner.VCPU_S_PER_JOB_FIXED / planner.job_cost_vcpu_s(90), 0.45)

    def test_a_zero_length_song_still_costs_the_overhead(self):
        self.assertAlmostEqual(planner.job_cost_vcpu_s(0), planner.VCPU_S_PER_JOB_FIXED)

    def test_shards_are_priced_on_their_own_songs_not_a_fleet_average(self):
        """plan_shards is longest-first, so shards differ in mean duration; pricing the fleet
        on one average would undercharge the long shard and overcharge the short one."""
        long_shard = [{"songs": ["a"], "duration_total_s": 300}]
        short_shard = [{"songs": ["b"], "duration_total_s": 60}]
        both = [{"songs": ["a"], "duration_total_s": 300}, {"songs": ["b"], "duration_total_s": 60}]
        self.assertAlmostEqual(planner.estimate(both, 10)[0],
                               planner.estimate(long_shard, 10)[0] + planner.estimate(short_shard, 10)[0])
        self.assertGreater(planner.estimate(long_shard, 10)[0], planner.estimate(short_shard, 10)[0])

    def test_a_limited_run_is_priced_at_the_shard_mean(self):
        shards = [{"songs": ["a", "b"], "duration_total_s": 300}]
        cpu_h, _ = planner.estimate(shards, 566, limit=3)
        self.assertAlmostEqual(cpu_h, 3 * planner.job_cost_vcpu_s(150) / 3600)

    def test_over_subscription_discounts_only_the_overhead(self):
        """2x overlaps per-job process overhead, not DSP — which is why the saving shrinks as
        songs lengthen (29% at a 42 s mean, ~11% at 237 s). A flat discount would misprice
        long-song runs badly."""
        for mean_s, one_x, two_x in ((42, 13.62, 9.71), (90, 18.76, 14.85), (237, 34.49, 30.58)):
            self.assertAlmostEqual(planner.job_cost_vcpu_s(mean_s), one_x, delta=0.06)
            self.assertAlmostEqual(planner.job_cost_vcpu_s(mean_s, 2), two_x, delta=0.06)
        short = 1 - planner.job_cost_vcpu_s(42, 2) / planner.job_cost_vcpu_s(42)
        long = 1 - planner.job_cost_vcpu_s(237, 2) / planner.job_cost_vcpu_s(237)
        self.assertGreater(short, long)

    def test_the_dsp_term_is_untouched_by_the_factor(self):
        """The gain must apply to the fixed term alone; if it scaled the whole cost, the
        saving would be duration-independent and the long-song prediction would be wrong."""
        d = 1000.0
        gap = planner.job_cost_vcpu_s(d) - planner.job_cost_vcpu_s(d, 2)
        self.assertAlmostEqual(gap, planner.VCPU_S_PER_JOB_FIXED
                               * (1 - 1 / planner.OVERHEAD_GAIN_AT_2X), delta=0.01)

    def test_no_credit_is_taken_beyond_the_measured_2x(self):
        """Nothing has tested 3x, and guessing upward would UNDER-price a run — the wrong
        direction for a spend ceiling."""
        self.assertEqual(planner.overhead_gain(3), planner.overhead_gain(2))
        self.assertEqual(planner.overhead_gain(10), planner.overhead_gain(2))

    def test_one_worker_per_core_and_absent_are_the_same_thing(self):
        for f in (None, 0, 1, 0.5):        # a sub-1 factor cannot make a job cheaper
            self.assertAlmostEqual(planner.overhead_gain(f), 1.0)
            self.assertAlmostEqual(planner.job_cost_vcpu_s(90, f), planner.job_cost_vcpu_s(90))

    def test_the_estimate_carries_the_factor_through(self):
        shards = [{"songs": ["a"], "duration_total_s": 42}]
        base, _ = planner.estimate(shards, 100)
        over, _ = planner.estimate(shards, 100, worker_factor=2)
        self.assertLess(over, base)
        self.assertAlmostEqual(over / base, planner.job_cost_vcpu_s(42, 2) / planner.job_cost_vcpu_s(42))

    def test_a_shard_with_no_duration_falls_back_rather_than_dividing_by_zero(self):
        shards = [{"songs": ["a"]}]
        self.assertAlmostEqual(planner.estimate(shards, 2)[0],
                               2 * planner.job_cost_vcpu_s(planner.DEFAULT_DURATION_S) / 3600)
        self.assertEqual(planner.estimate([{"songs": []}], 5), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
