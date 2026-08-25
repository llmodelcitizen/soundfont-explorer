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


class FakeS3:
    def __init__(self):
        self.puts: list = []

    def put_object(self, **kw):
        self.puts.append(kw)


class FakeCfg:
    def __init__(self, repo: str):
        self.repo = repo
        self.render_enabled = True
        self.log_group = "/aws/batch/test"
        self.compute_env = "ce"
        self.fonts_bucket = "fonts"
        self.s3 = FakeS3()


class FakeManager(renders.RunManager):
    """RunManager with the AWS edges stubbed: records are kept in memory, the fleet is
    never touched, and the finisher is a hook the tests control."""

    def __init__(self, repo: str = "/nonexistent"):
        self.cfg = FakeCfg(repo)
        self.lock = threading.RLock()
        self.runs: dict = {}
        self._watching: set = set()
        self._submitting: set = set()
        self._finishing: set = set()
        self._loaded = True
        self.puts: list = []
        self.slept: list = []
        self.watched: list = []

    def _put(self, rec):
        self.puts.append(json.loads(json.dumps(rec)))

    def _sleep_fleet(self, rec):
        self.slept.append(rec["run_id"])

    def _scan_published(self, rec):
        pass

    def _watch_async(self, rid):
        self.watched.append(rid)


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

    def test_the_watcher_keeps_the_polls_verdict_through_the_finisher(self):
        """_poll's "Batch forgot this job" note is the operator's only explanation of the
        run; _watch calls finish() one line later and finish() used to replace the whole
        finisher dict, so the SPA showed a failed run with a green finisher chip (#19)."""
        from sfadmin import publishops
        with unittest.mock.patch.object(renders, "POLL_S", 0), \
                unittest.mock.patch.object(publishops, "sync_down", lambda: None), \
                unittest.mock.patch.object(publishops, "rebuild_and_publish", lambda: {}):
            self.m._watch("r1", FakeBatch([]))          # the real sequence: poll, park, finish
        rec = self.m.runs["r1"]
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(rec["status_summary"], {"UNKNOWN": 1})
        self.assertIn("unknown to describe_jobs", rec["finisher"]["error"])
        self.assertTrue(rec["finisher"]["songs_json_published"])   # it did still republish
        self.assertIn("unknown to describe_jobs", self.m.puts[-1]["finisher"]["error"])
        self.assertEqual(self.m.slept, ["r1"])

    def test_terminate_stops_the_poll(self):
        batch = FakeBatch([{"status": "RUNNING"}] * 5)

        def sleep(s):
            self.rec["state"] = "terminated"                    # terminate() from another thread

        self.m._poll(self.rec, batch, sleep)
        self.assertEqual(batch.calls, 1)


class ReconcileTests(unittest.TestCase):
    """The crash heuristic (no job id after 300 s ⇒ failed) must not fire on a submit that
    is still staging in this process (#19)."""

    def setUp(self):
        self.m = FakeManager()
        self.t0 = renders._ms("2026-08-24T00:00:00Z") / 1000
        # 10 minutes into a submit whose aws s3 sync is still running
        self.m.runs["r1"] = run_record("r1", "staged", batch_job_id=None)

    def test_in_flight_submit_is_left_alone_and_counts_as_live(self):
        self.m._submitting.add("r1")
        self.m._reconcile_once(now=self.t0 + 600)   # no boto3 here: live ⇒ the CE is not touched
        self.assertEqual(self.m.runs["r1"]["state"], "staged")
        self.assertEqual(self.m.slept, [])
        self.assertEqual(self.m.puts, [])

    def test_crashed_submit_is_failed_after_the_grace_period(self):
        with unittest.mock.patch.dict(sys.modules, {"boto3": unittest.mock.MagicMock()}):
            self.m._reconcile_once(now=self.t0 + 200)
            self.assertEqual(self.m.runs["r1"]["state"], "staged")   # still within 300 s
            self.m._reconcile_once(now=self.t0 + 600)
        self.assertEqual(self.m.runs["r1"]["state"], "failed")
        self.assertIn("crash during submit", self.m.runs["r1"]["finisher"]["error"])
        self.assertEqual(self.m.slept, ["r1"])

    def test_a_wedged_submit_stops_being_exempt(self):
        """The exemption is bounded: a submit whose staging never returns used to keep the
        run live for ever, which blocks every later submit and parks no compute (#19)."""
        self.m._submitting.add("r1")
        with unittest.mock.patch.dict(sys.modules, {"boto3": unittest.mock.MagicMock()}):
            self.m._reconcile_once(now=self.t0 + renders.SUBMIT_EXEMPT_S - 60)
            self.assertEqual(self.m.runs["r1"]["state"], "staged")
            self.m._reconcile_once(now=self.t0 + renders.SUBMIT_EXEMPT_S + 60)
        self.assertEqual(self.m.runs["r1"]["state"], "failed")
        self.assertIn("wedged", self.m.runs["r1"]["finisher"]["error"])
        self.assertIsNone(self.m.active())          # and submits are possible again

    def test_staging_syncs_have_a_timeout(self):
        # subprocess.run(check=True) with no timeout is what wedges a submit
        with unittest.mock.patch.object(renders.subprocess, "run") as run:
            self.m._stage([{"songs": ["a"]}])
        self.assertTrue(run.call_args_list)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs.get("timeout"), renders.STAGE_TIMEOUT_S)

    def test_live_run_is_readopted(self):
        self.m.runs["r1"]["batch_job_id"] = "job-9"
        self.m._reconcile_once(now=self.t0 + 600)
        self.assertEqual(self.m.watched, ["r1"])
        self.assertEqual(self.m.runs["r1"]["state"], "staged")


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

    def test_a_published_tab_rebuild_cannot_run_beside_the_finisher(self):
        """The mutex is the resource's, not finish()'s: the Published tab's routes take the
        same publocks slot. Only _finishing did before, and the routes never looked at it —
        so a rebuild that passed the "no active run" check a moment before the watcher
        entered finish() rewrote songs.json beside it (#19)."""
        from sfadmin import publocks
        t = threading.Thread(target=self.m.finish, args=("r1",))
        t.start()
        self.assertTrue(self.entered.wait(5))
        self.assertIsNotNone(publocks.held_by())
        self.assertIn("r1", publocks.held_by()[0])
        with self.assertRaises(publocks.Busy):        # exactly what routes_publish does
            with publocks.exclusive("POST /api/published/rebuild"):
                self.fail("two writers on songs.json")
        self.release.set()
        t.join(5)
        self.assertIsNone(publocks.held_by())
        with publocks.exclusive("POST /api/published/rebuild"):
            pass                                      # freed once the finisher is done

    def test_a_finisher_that_cannot_get_the_mutex_reports_it(self):
        from sfadmin import publocks
        self.release.set()
        with publocks.exclusive("POST /api/published/prune"):
            rec = self.m.finish("r1", lock_wait=0.2)  # bounded: no wedged watcher thread
        self.assertFalse(rec["finisher"]["songs_json_published"])
        self.assertIn("another publish is in progress", rec["finisher"]["error"])
        self.assertEqual(self.rebuilds, 0)
        self.assertFalse(self.m.finishing("r1"))

    def test_a_verdict_recorded_before_the_finisher_survives_it(self):
        self.release.set()
        self.m.runs["r1"]["finisher"]["error"] = "instance-type restore failed: boom"
        rec = self.m.finish("r1")
        self.assertTrue(rec["finisher"]["songs_json_published"])
        self.assertEqual(rec["finisher"]["error"], "instance-type restore failed: boom")


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
    variants += [{"id": "sc55-1", "engine": "sc55", "publish": True, "requires_rom": True,
                  "facets": {"completeness": "full_gm"}}]
    (p / "catalog" / "variants.json").write_text(json.dumps({"variants": variants}))
    (p / "render" / "engines.json").write_text(json.dumps({"engines": {
        "fluidsynth": {"version": "2"}, "adlmidi": {"version": "1"},
        "sc55": {"version": "1"}}}))


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

    def test_engine_subset_shrinks_the_estimate(self):
        self.assertEqual(self.m.plan(["a", "b"], 1, engines=["adlmidi"])["jobs"], 2)
        self.assertEqual(self.m.plan(["a"], 1, engines=["fluidsynth"])["jobs"], 4)
        self.assertEqual(self.m.plan(["a"], 1, engines=["fluidsynth", "adlmidi"])["jobs"], 5)

    def test_limit_is_counted_per_shard_not_per_song(self):
        # shard.py passes --limit to ONE `sfr render` per shard and sfr truncates the
        # shard's flat job list once, so 2 songs in 1 shard at --limit 3 is 3 jobs, not 6
        one = self.m.plan(["a", "b"], 1, limit=3)
        self.assertEqual(len(one["shards"]), 1)
        self.assertEqual(one["jobs"], 3)
        two = self.m.plan(["a", "b"], 2, limit=3)          # one song per shard: 3 + 3
        self.assertEqual(len(two["shards"]), 2)
        self.assertEqual(two["jobs"], 6)
        self.assertEqual(self.m.plan(["a"], 1, engines=["fluidsynth"], limit=3)["jobs"], 3)
        self.assertEqual(one["cpu_h"], round(3 * planner.CPU_S_PER_JOB / 3600, 1))

    def test_explicit_variants_override_still_capped_by_limit(self):
        self.assertEqual(self.m.plan(["a"], 1, variants=40)["jobs"], 40)
        self.assertEqual(self.m.plan(["a"], 1, variants=40, limit=7)["jobs"], 7)

    def test_rejects_unknown_engines_and_songs(self):
        with self.assertRaises(ValueError):
            self.m.plan(["a"], 1, engines=["adlmid"])
        with self.assertRaises(ValueError):
            self.m.plan(["a", "zzz"], 1)

    def test_a_rom_engine_is_refused_with_the_real_reason(self):
        # sc55 is a real, pinned engine, but roms/ is empty on the fleet: "unknown engine"
        # sent the operator looking for a typo (#19)
        with self.assertRaises(ValueError) as cm:
            self.m.plan(["a"], 1, engines=["sc55"])
        self.assertIn("cannot run on the fleet", str(cm.exception))
        self.assertIn("roms/", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
