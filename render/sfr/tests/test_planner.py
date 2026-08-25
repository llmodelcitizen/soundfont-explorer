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
        shards = [{"songs": ["a", "b"], "duration_total_s": 1}]
        cpu_h, usd = planner.estimate(shards, 566)
        self.assertAlmostEqual(cpu_h, 2 * 566 * 53.0 / 3600)
        self.assertAlmostEqual(usd, cpu_h * 0.019)


if __name__ == "__main__":
    unittest.main()
