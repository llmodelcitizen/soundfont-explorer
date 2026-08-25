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
import unittest.mock

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
        self._finishing: set = set()
        self._finish_lock = threading.Lock()
        self._loaded = True
        self.puts: list = []
        self.slept: list = []

    def _put(self, rec):
        self.puts.append(json.loads(json.dumps(rec)))

    def _sleep_fleet(self, rec):
        self.slept.append(rec["run_id"])

    def _scan_published(self, rec):
        pass


def run_record(rid: str, state: str, **kw) -> dict:
    rec = {"schema": 1, "run_id": rid, "state": state, "songs": ["a"], "shards": [],
           "knobs": {}, "estimate": {}, "batch_job_id": "job-1",
           "submitted_at": "2026-08-24T00:00:00Z", "finished_at": None,
           "status_summary": {}, "published_sets": {},
           "instance_types_before": None, "instance_types_restored": True,
           "finisher": {"ran_at": None, "songs_json_published": False, "error": None}}
    rec.update(kw)
    return rec


class FakeBatch:
    """describe_jobs scripted per call: a dict = one job, [] = unknown job, Exception = API error."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def describe_jobs(self, jobs):
        self.calls += 1
        item = self.script.pop(0) if self.script else []
        if isinstance(item, Exception):
            raise item
        return {"jobs": [item] if item else []}


class WatchTests(unittest.TestCase):
    """_poll(): a job Batch no longer knows ends the run instead of being retried forever (#19)."""

    def setUp(self):
        self.m = FakeManager()
        self.rec = run_record("r1", "running")
        self.m.runs["r1"] = self.rec
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)

    def test_terminal_status_ends_the_run(self):
        batch = FakeBatch([{"status": "RUNNING"},
                           {"status": "SUCCEEDED", "arrayProperties": {"statusSummary": {"SUCCEEDED": 2}}}])
        self.m._poll(self.rec, batch, self.sleep)
        self.assertEqual(self.rec["state"], "succeeded")
        self.assertEqual(self.rec["status_summary"], {"SUCCEEDED": 2})
        self.assertIsNotNone(self.rec["finished_at"])
        self.assertEqual(len(self.sleeps), 1)

    def test_unknown_job_fails_the_run_after_the_grace_polls(self):
        batch = FakeBatch([])  # every answer: jobs == []
        self.m._poll(self.rec, batch, self.sleep)
        self.assertEqual(batch.calls, renders.MISSING_JOB_POLLS)
        self.assertEqual(self.rec["state"], "failed")
        self.assertEqual(self.rec["status_summary"], {"UNKNOWN": 1})
        self.assertIn("unknown to describe_jobs", self.rec["finisher"]["error"])
        self.assertIsNotNone(self.rec["finished_at"])
        self.assertIsNone(self.m.active())                      # submits/prune unblocked
        self.assertEqual(self.m.puts[-1]["state"], "failed")

    def test_a_brief_gap_or_api_error_is_tolerated(self):
        batch = FakeBatch([[], RuntimeError("throttled"), [], {"status": "RUNNING"}, [],
                           {"status": "FAILED"}])
        self.m._poll(self.rec, batch, self.sleep)
        self.assertEqual(self.rec["state"], "failed")
        self.assertEqual(self.rec["status_summary"], {"FAILED": 1})
        self.assertIsNone(self.rec["finisher"]["error"])       # a real verdict, not "unknown"

    def test_terminate_stops_the_poll(self):
        batch = FakeBatch([{"status": "RUNNING"}] * 5)

        def sleep(s):
            self.rec["state"] = "terminated"                    # terminate() from another thread

        self.m._poll(self.rec, batch, sleep)
        self.assertEqual(batch.calls, 1)


class LogTailTests(unittest.TestCase):
    """logs() returns the newest events of the run, scoped to the run's window (#19)."""

    def test_tail_keeps_the_last_events_across_pages(self):
        pages = [{"events": [{"timestamp": i, "message": f"line {i}\n"} for i in range(0, 150)]},
                 {"events": [{"timestamp": i, "message": f"line {i}"} for i in range(150, 230)]},
                 {}]
        tail = renders._tail_events(pages, 100)
        self.assertEqual(len(tail), 100)
        self.assertEqual(tail[0], {"t": 130, "msg": "line 130"})
        self.assertEqual(tail[-1], {"t": 229, "msg": "line 229"})
        self.assertEqual(renders._tail_events([{"events": []}], 100), [])

    def test_query_window_follows_the_run(self):
        import datetime
        start = int(datetime.datetime(2026, 8, 24, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        rec = run_record("r1", "running")
        q = renders._log_query(rec, "/aws/batch/x")
        self.assertEqual(q, {"logGroupName": "/aws/batch/x", "startTime": start})
        rec["finished_at"] = "2026-08-24T02:00:00Z"
        q = renders._log_query(rec, "/aws/batch/x")
        self.assertEqual(q["endTime"], start + 2 * 3600 * 1000 + renders.LOG_END_SLACK_MS)


class FinisherTests(unittest.TestCase):
    """finish() is single-writer and a finishing run still counts as active (#19)."""

    def setUp(self):
        from sfadmin import publishops
        self.m = FakeManager()
        self.m.runs["r1"] = run_record("r1", "succeeded", finished_at="2026-08-24T01:00:00Z")
        self.entered = threading.Event()
        self.release = threading.Event()
        self.rebuilds = 0

        def sync_down():
            self.entered.set()
            self.release.wait(5)

        def rebuild_and_publish():
            self.rebuilds += 1
            return {}

        for name, fn in (("sync_down", sync_down), ("rebuild_and_publish", rebuild_and_publish)):
            patcher = unittest.mock.patch.object(publishops, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_finishing_run_is_active_and_a_second_finish_is_refused(self):
        self.assertIsNone(self.m.active())                      # terminal, nothing running
        t = threading.Thread(target=self.m.finish, args=("r1",))
        t.start()
        self.assertTrue(self.entered.wait(5))
        self.assertEqual(self.m.active()["run_id"], "r1")       # finishing ⇒ still active
        self.assertTrue(self.m.finishing("r1"))
        with self.assertRaises(RuntimeError):
            self.m.finish("r1")                                 # no concurrent rewrite
        with self.assertRaises(RuntimeError) as cm:
            self.m.submit(["a"], 1, 60.0)                       # and no new run meanwhile
        self.assertIn("finishing", str(cm.exception))
        self.release.set()
        t.join(5)
        self.assertEqual(self.rebuilds, 1)
        self.assertIsNone(self.m.active())
        self.assertFalse(self.m.finishing("r1"))
        self.assertTrue(self.m.runs["r1"]["finisher"]["songs_json_published"])
        self.assertEqual(self.m.puts[-1]["finisher"]["error"], None)

    def test_finisher_error_is_recorded_and_the_slot_freed(self):
        from sfadmin import publishops
        self.release.set()
        with unittest.mock.patch.object(publishops, "rebuild_and_publish",
                                        side_effect=RuntimeError("manifest failed")):
            rec = self.m.finish("r1")
        self.assertEqual(rec["finisher"]["error"], "manifest failed")
        self.assertFalse(rec["finisher"]["songs_json_published"])
        self.assertFalse(self.m.finishing("r1"))
        self.assertIsNone(self.m.active())


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
