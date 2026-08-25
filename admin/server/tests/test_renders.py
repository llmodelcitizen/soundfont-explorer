"""RunManager logic that needs no AWS: planning/estimates and the run-state bookkeeping.
Stdlib only — boto3 is absent here, so every S3/Batch touchpoint is overridden on a
subclass that skips RunManager.__init__ (no reconciler thread, no config lookup)."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "render", "cloud"))
os.environ.setdefault("SFADMIN_BUCKET", "test")
os.environ.setdefault("SFADMIN_HOSTNAME", "test")
import planner  # noqa: E402,F401  (pre-imported so renders._planner() resolves without a repo path)
from sfadmin import renders  # noqa: E402


class FakeCfg:
    def __init__(self, repo: str):
        self.repo = repo
        self.render_enabled = True
        self.log_group = "/aws/batch/test"
        self.compute_env = "ce"


class FakeManager(renders.RunManager):
    """RunManager with the AWS edges stubbed: records are kept in memory, the fleet is
    never touched, and the finisher is a hook the tests control."""

    def __init__(self, repo: str = "/nonexistent"):
        self.cfg = FakeCfg(repo)
        self.lock = threading.RLock()
        self.runs: dict = {}
        self._watching: set = set()
        self._loaded = True
        self.puts: list = []
        self.slept: list = []

    def _put(self, rec):
        self.puts.append(json.loads(json.dumps(rec)))

    def _sleep_fleet(self, rec):
        self.slept.append(rec["run_id"])

    def _scan_published(self, rec):
        pass


def write_repo(root: str) -> None:
    p = pathlib.Path(root)
    (p / "songs").mkdir(parents=True)
    (p / "catalog").mkdir()
    (p / "render").mkdir()
    (p / "songs" / "songs.json").write_text(json.dumps({"songs": [
        {"id": "a", "duration_s": 100}, {"id": "b", "duration_s": 200}]}))
    variants = [{"id": f"sf2-{i}", "engine": "fluidsynth", "publish": True,
                 "facets": {"completeness": "full_gm"}} for i in range(4)]
    variants += [{"id": "adl-b0", "engine": "adlmidi", "publish": True,
                  "facets": {"completeness": "full_gm"}}]
    (p / "catalog" / "variants.json").write_text(json.dumps({"variants": variants}))
    (p / "render" / "engines.json").write_text(json.dumps({"engines": {
        "fluidsynth": {"version": "2"}, "adlmidi": {"version": "1"}}}))


class PlanTests(unittest.TestCase):
    """The estimate honours engines/limit and counts variants from the catalog (#19)."""

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        write_repo(self.repo)
        self.m = FakeManager(self.repo)

    def test_default_count_comes_from_the_catalog(self):
        est = self.m.plan(["a", "b"], 2)
        self.assertEqual(est["variants_per_song"], 5)
        self.assertEqual(est["jobs"], 10)
        self.assertEqual(len(est["shards"]), 2)
        self.assertEqual(est["cpu_h"], round(10 * planner.CPU_S_PER_JOB / 3600, 1))

    def test_engine_subset_and_limit_shrink_the_estimate(self):
        self.assertEqual(self.m.plan(["a", "b"], 1, engines=["adlmidi"])["jobs"], 2)
        self.assertEqual(self.m.plan(["a", "b"], 1, limit=3)["jobs"], 6)
        self.assertEqual(self.m.plan(["a"], 1, engines=["fluidsynth"], limit=3)["jobs"], 3)
        self.assertEqual(self.m.plan(["a"], 1, engines=["fluidsynth", "adlmidi"])["jobs"], 5)

    def test_explicit_variants_override_still_capped_by_limit(self):
        self.assertEqual(self.m.plan(["a"], 1, variants=40)["jobs"], 40)
        self.assertEqual(self.m.plan(["a"], 1, variants=40, limit=7)["jobs"], 7)

    def test_rejects_unknown_engines_and_songs(self):
        with self.assertRaises(ValueError):
            self.m.plan(["a"], 1, engines=["adlmid"])
        with self.assertRaises(ValueError):
            self.m.plan(["a", "zzz"], 1)


if __name__ == "__main__":
    unittest.main()
