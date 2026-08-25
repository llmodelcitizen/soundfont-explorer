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
import time
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
           "instance_types_before": None, "instance_types_restored": True, "verdict": None,
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

    def test_the_missing_count_is_consecutive_empties_only(self):
        """An API error between two empty answers is not evidence Batch has forgotten the
        job. Without a reset, empties spread over hours by intermittent throttling added up
        to the grace count and failed a live run — parking the fleet under it (#19)."""
        script = []
        for _ in range(renders.MISSING_JOB_POLLS * 2):     # never two empties in a row
            script += [[], RuntimeError("throttled")]
        script.append({"status": "SUCCEEDED"})
        self.m._poll(self.rec, FakeBatch(script), self.sleep)
        self.assertEqual(self.rec["state"], "succeeded")
        self.assertIsNone(self.rec["finisher"]["error"])
        self.assertIsNone(self.rec["verdict"])

    def test_describe_jobs_that_never_answers_ends_the_run_too(self):
        """A persistent failure (revoked credentials, a queue that no longer exists) used
        to be retried for ever, so the run stayed non-terminal and every submit and publish
        409'd until someone found the manual Terminate button (#19)."""
        batch = FakeBatch([RuntimeError("ExpiredToken")] * (renders.POLL_ERROR_POLLS + 5))
        self.m._poll(self.rec, batch, self.sleep)
        self.assertEqual(batch.calls, renders.POLL_ERROR_POLLS)
        self.assertEqual(self.rec["state"], "failed")
        self.assertIn("ExpiredToken", self.rec["verdict"])
        self.assertIn("may still be running", self.rec["finisher"]["error"])
        self.assertIsNone(self.m.active())                     # submits/publish unblocked
        # and it is much more patient than the forgotten-job grace: an API error says
        # nothing about the run, while calling a live run failed parks the fleet
        self.assertGreater(renders.POLL_ERROR_POLLS, renders.MISSING_JOB_POLLS)

    def test_one_good_answer_resets_the_error_run(self):
        script = ([RuntimeError("throttled")] * (renders.POLL_ERROR_POLLS - 1)
                  + [{"status": "RUNNING"}]
                  + [RuntimeError("throttled")] * (renders.POLL_ERROR_POLLS - 1)
                  + [{"status": "SUCCEEDED"}])
        self.m._poll(self.rec, FakeBatch(script), self.sleep)
        self.assertEqual(self.rec["state"], "succeeded")

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


class SleepFleetTests(unittest.TestCase):
    """_sleep_fleet runs one line after _poll in _watch, so its note must not replace the
    poll's verdict — finish() reads that a moment later (#19)."""

    def test_a_restore_failure_is_added_to_the_verdict_not_written_over_it(self):
        m = FakeManager()
        rec = run_record("r1", "failed", instance_types_before=["c7a.large"],
                         instance_types_restored=False)
        renders._note_verdict(rec, "Batch job job-1 is unknown to describe_jobs")
        batch = unittest.mock.MagicMock()
        batch.describe_compute_environments.return_value = {"computeEnvironments": [{"status": "VALID"}]}
        # every attempt fails: one transient refusal is now retried past (#42), so a verdict
        # note only appears when the restore genuinely cannot be done
        batch.update_compute_environment.side_effect = RuntimeError("boom")
        boto3 = unittest.mock.MagicMock()
        boto3.client.return_value = batch
        with unittest.mock.patch.dict(sys.modules, {"boto3": boto3}), \
             unittest.mock.patch.object(renders.time, "sleep", lambda _: None):
            renders.RunManager._sleep_fleet(m, rec)     # the real one; FakeManager stubs it
        self.assertIn("unknown to describe_jobs", rec["verdict"])
        self.assertIn("instance-type restore failed: boom", rec["verdict"])
        self.assertEqual(rec["finisher"]["error"], rec["verdict"])


class CeSettleTests(unittest.TestCase):
    """Batch refuses a second change while one is in flight, so every update has to wait (#42).

    The live symptom: submitting with an instance-type override failed with "Cannot update,
    compute environment ... is being modified", and the restore that runs on the failure path
    hit the same wall — leaving the fleet on the run's instance types.
    """

    def batch_with(self, statuses):
        batch = unittest.mock.MagicMock()
        batch.describe_compute_environments.side_effect = [
            {"computeEnvironments": [{"status": st}]} for st in statuses]
        return batch

    def test_waits_while_the_environment_is_updating(self):
        m = FakeManager()
        batch = self.batch_with(["UPDATING", "UPDATING", "VALID"])
        slept = []
        status = renders.RunManager._wait_ce_settled(m, batch, sleep=slept.append)
        self.assertEqual(status, "VALID")
        self.assertEqual(batch.describe_compute_environments.call_count, 3)
        self.assertEqual(len(slept), 2)

    def test_returns_immediately_when_already_settled(self):
        m = FakeManager()
        batch = self.batch_with(["VALID"])
        self.assertEqual(renders.RunManager._wait_ce_settled(m, batch, sleep=lambda _: None), "VALID")
        self.assertEqual(batch.describe_compute_environments.call_count, 1)

    def test_gives_up_rather_than_blocking_for_ever(self):
        m = FakeManager()
        batch = unittest.mock.MagicMock()
        batch.describe_compute_environments.return_value = {"computeEnvironments": [{"status": "UPDATING"}]}
        status = renders.RunManager._wait_ce_settled(m, batch, timeout_s=0.05, sleep=lambda _: None)
        self.assertEqual(status, "UPDATING")

    def test_the_restore_retries_past_a_still_settling_environment(self):
        """The failure path runs right after a refused update, so the first try often loses."""
        m = FakeManager()
        rec = run_record("r1", "failed", instance_types_before=["c7a.24xlarge"],
                         instance_types_restored=False)
        batch = unittest.mock.MagicMock()
        batch.describe_compute_environments.return_value = {"computeEnvironments": [{"status": "VALID"}]}
        boom = RuntimeError("Cannot update, compute environment is being modified.")
        batch.update_compute_environment.side_effect = [boom, None, None]
        boto3 = unittest.mock.MagicMock()
        boto3.client.return_value = batch
        with unittest.mock.patch.dict(sys.modules, {"boto3": boto3}), \
             unittest.mock.patch.object(renders.time, "sleep", lambda _: None):
            renders.RunManager._sleep_fleet(m, rec)
        self.assertTrue(rec["instance_types_restored"], rec.get("verdict"))
        self.assertNotIn("restore failed", rec.get("verdict") or "")

    def test_a_restore_that_never_succeeds_is_still_reported(self):
        m = FakeManager()
        rec = run_record("r1", "failed", instance_types_before=["c7a.24xlarge"],
                         instance_types_restored=False)
        batch = unittest.mock.MagicMock()
        batch.describe_compute_environments.return_value = {"computeEnvironments": [{"status": "VALID"}]}
        batch.update_compute_environment.side_effect = RuntimeError("still modifying")
        boto3 = unittest.mock.MagicMock()
        boto3.client.return_value = batch
        with unittest.mock.patch.dict(sys.modules, {"boto3": boto3}), \
             unittest.mock.patch.object(renders.time, "sleep", lambda _: None):
            renders.RunManager._sleep_fleet(m, rec)
        self.assertFalse(rec["instance_types_restored"])
        self.assertIn("instance-type restore failed", rec["verdict"])


class ActivePhaseTests(unittest.TestCase):
    """routes_publish needs the run AND its phase from one snapshot: re-asking finishing()
    afterwards reported "a render run is live" for a run that had just finished (#19)."""

    def setUp(self):
        self.m = FakeManager()

    def test_phase_comes_back_with_the_run(self):
        self.assertIsNone(self.m.active_phase())
        self.m.runs["r1"] = run_record("r1", "running")
        self.assertEqual(self.m.active_phase(), (self.m.runs["r1"], False))
        self.m.runs["r1"]["state"] = "failed"
        self.assertIsNone(self.m.active_phase())
        self.m._finishing.add("r1")
        self.assertEqual(self.m.active_phase(), (self.m.runs["r1"], True))

    def test_the_publish_routes_do_not_re_ask(self):
        # routes_publish needs FastAPI (absent here), so this is a source check: a second
        # read of _finishing is the two-step this fix removed
        path = os.path.join(HERE, "..", "sfadmin", "routes_publish.py")
        with open(path) as fh:
            body = fh.read()
        self.assertIn("active_phase()", body)
        self.assertNotIn(".finishing(", body)


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

    def test_the_backstop_leaves_room_for_two_full_length_stage_syncs(self):
        """_stage runs TWO syncs, each allowed STAGE_TIMEOUT_S. At a backstop of exactly
        that sum, a slow-but-healthy submit is called wedged: the fleet is parked under it
        and the record keeps a finished_at in the past, which puts the run's log window
        behind it and empties the Logs box for ever (#19)."""
        self.assertGreater(renders.SUBMIT_EXEMPT_S, 2 * renders.STAGE_TIMEOUT_S)

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


def ev(t, msg, stream="s/0"):
    return {"t": t, "msg": msg, "stream": stream}


class ShardStateTests(unittest.TestCase):
    """The Logs view is a per-shard status board, not a log tail: these are the real line
    shapes from the 2026-08-25 run, verbatim."""

    def fold(self, events):
        return {s["shard"]: s for s in renders.shard_states(events)}

    def test_a_shard_mid_render(self):
        got = self.fold([
            ev(1, "[shard 6] 2 songs: misc-slayer-black-magic misc-slayer-dittohead  "
                  "workers=32 mem_units=186"),
            ev(2, "[shard 6] staged 500 fonts (46.4 GiB), songs and catalog in 80s"),
            ev(3, "[sfr] 1132 jobs, 32 workers, 186 memory units"),
            ev(4, "[sfr] 633/1132 done=633 failed=0 skipped=0 running=32 mem_free=169 "
                  "elapsed=380s eta=292s  misc-slayer-black-magic/sf2-33b991e762(23s)"),
        ])[6]
        self.assertEqual(got["phase"], "rendering")
        self.assertEqual((got["done"], got["total"], got["failed"]), (633, 1132, 0))
        self.assertEqual((got["running"], got["workers"]), (32, 32))
        self.assertEqual((got["eta_s"], got["elapsed_s"]), (292, 380))
        self.assertEqual((got["staged_fonts"], got["stage_s"]), (500, 80))
        self.assertEqual(got["staged_gib"], 46.4)
        self.assertEqual(got["songs"], ["misc-slayer-black-magic", "misc-slayer-dittohead"])

    def test_the_latest_progress_line_wins(self):
        got = self.fold([
            ev(1, "[sfr] 27/566 done=27 failed=0 skipped=0 running=32 elapsed=10s eta=1458s"),
            ev(2, "[sfr] 193/566 done=193 failed=2 skipped=0 running=32 elapsed=120s eta=584s"),
        ])[None]
        self.assertEqual((got["done"], got["failed"], got["eta_s"]), (193, 2, 584))

    def test_sfr_lines_are_attributed_by_stream_not_by_content(self):
        """`[sfr]` carries no shard number — without the stream, eight shards fold into one."""
        got = self.fold([
            ev(1, "[shard 0] 1 songs: a  workers=32 mem_units=186", "s/0"),
            ev(2, "[sfr] 336/566 done=336 failed=0 skipped=0 running=32 elapsed=1s eta=233s", "s/0"),
            ev(3, "[shard 7] 2 songs: b c  workers=32 mem_units=186", "s/7"),
            ev(4, "[sfr] 444/1132 done=444 failed=0 skipped=0 running=32 elapsed=1s eta=558s", "s/7"),
        ])
        self.assertEqual(got[0]["done"], 336)
        self.assertEqual(got[7]["done"], 444)
        self.assertEqual(got[0]["total"], 566)
        self.assertEqual(got[7]["total"], 1132)

    def test_the_phases_walk_forward_to_done(self):
        base = [ev(1, "[shard 3] 1 songs: a  workers=32 mem_units=186")]
        self.assertEqual(self.fold(base)[3]["phase"], "staging")
        base.append(ev(2, "[shard 3] staged 500 fonts (46.4 GiB), songs and catalog in 80s"))
        self.assertEqual(self.fold(base)[3]["phase"], "rendering")
        base.append(ev(3, "[shard 3] render finished rc=0 in 700s"))
        self.assertEqual(self.fold(base)[3]["phase"], "publishing")
        base.append(ev(4, "[shard 3] shard complete (render rc=0; only expected `silent` failures)"))
        got = self.fold(base)[3]
        self.assertEqual(got["phase"], "done")
        self.assertEqual((got["render_rc"], got["render_s"]), (0, 700))

    def test_published_songs_accumulate(self):
        got = self.fold([
            ev(1, "[shard 2] published song-one: 566 variants in 90s"),
            ev(2, "[shard 2] published song-two: 540 variants in 80s"),
        ])[2]
        self.assertEqual([p["song"] for p in got["published"]], ["song-one", "song-two"])
        self.assertEqual(got["published"][0]["variants"], 566)

    def test_bang_lines_are_surfaced_as_problems(self):
        """`!!` is how shard.py reports every failure it can still describe."""
        got = self.fold([
            ev(1, "[shard 4] !! manifest failed for song-x rc=3: boom"),
            ev(2, "[shard 4] !! shard failed to publish: song-x"),
        ])[4]
        self.assertEqual(len(got["problems"]), 2)
        self.assertTrue(got["problems"][0].startswith("manifest failed for song-x"))

    def test_a_traceback_with_no_shard_prefix_is_still_a_problem(self):
        got = self.fold([ev(1, "Traceback (most recent call last):")])[None]
        self.assertEqual(len(got["problems"]), 1)

    def test_the_phases_summary_is_parsed_as_json(self):
        blob = '{"shard": 5, "wall_s": 1000.0, "tail_fraction": 0.26}'
        got = self.fold([ev(1, f"[shard 5] phases {blob}")])[5]
        self.assertEqual(got["phases"]["tail_fraction"], 0.26)
        self.assertEqual(got["phases"]["shard"], 5)

    def test_shards_come_back_in_index_order_with_unknowns_last(self):
        states = renders.shard_states([
            ev(1, "[shard 7] 1 songs: a  workers=32 mem_units=1", "s/7"),
            ev(2, "[sfr] 1/2 done=1 failed=0 skipped=0 running=1 elapsed=1s eta=1s", "s/?"),
            ev(3, "[shard 0] 1 songs: b  workers=32 mem_units=1", "s/0"),
        ])
        self.assertEqual([s["shard"] for s in states], [0, 7, None])

    def test_an_empty_tail_folds_to_nothing(self):
        self.assertEqual(renders.shard_states([]), [])


class LogQueryShapeTests(unittest.TestCase):
    """What logs() actually asks CloudWatch for. The filter is not cosmetic: unfiltered, the
    live 30-minute window was 25,993 events in 3.6 s (96.6% of it s5cmd `cp` receipts) against
    113 in 1.15 s filtered — on a sync route the SPA re-reads every 5 s per open box."""

    def setUp(self):
        self.m = FakeManager()
        self.m.runs["r1"] = run_record("r1", "running")
        self.captured = {}

        def paginate(**kw):
            self.captured.update(kw)
            return [{"events": [
                {"timestamp": 1, "message": "[shard 0] 1 songs: a  workers=32 mem_units=1",
                 "logStreamName": "s/0"},
                {"timestamp": 2, "message": "cp s3://b/x /scratch/x", "logStreamName": "s/0"},
            ]}]
        self.boto3 = unittest.mock.MagicMock()
        self.boto3.client.return_value.get_paginator.return_value.paginate.side_effect = paginate

    def logs(self, **kw):
        with unittest.mock.patch.dict(sys.modules, {"boto3": self.boto3}):
            return self.m.logs("r1", **kw)

    def test_the_signal_view_filters_in_cloudwatch(self):
        out = self.logs()
        self.assertEqual(self.captured["filterPattern"], renders.SIGNAL_PATTERN)
        self.assertNotIn("logStreamNames", self.captured)
        self.assertEqual(out["view"], "signal")
        self.assertEqual([s["shard"] for s in out["shards"]], [0])

    def test_the_filter_admits_every_line_shard_py_can_emit(self):
        """shard.py writes through log() ("[shard N] ...") or forwards sfr's "[sfr] ..."; the
        rest of the pattern is for output that never reaches either (a crash on stderr)."""
        for term in ('?"[sfr]"', '?"[shard"', '?"Traceback"'):
            self.assertIn(term, renders.SIGNAL_PATTERN)

    def test_a_raw_view_is_scoped_to_one_stream(self):
        out = self.logs(view="raw", stream="s/0")
        self.assertEqual(self.captured["logStreamNames"], ["s/0"])
        self.assertNotIn("filterPattern", self.captured)
        self.assertEqual(out["view"], "raw")
        self.assertEqual(out["shards"], [])          # a single stream is not a fleet fold
        self.assertEqual(len(out["events"]), 2)      # including the cp line: raw means raw

    def test_raw_without_a_stream_refuses_to_walk_the_whole_group(self):
        """Unfiltered AND unscoped is the 26,000-event walk this change exists to avoid."""
        out = self.logs(view="raw")
        self.assertEqual(out["view"], "signal")
        self.assertEqual(self.captured["filterPattern"], renders.SIGNAL_PATTERN)

    def test_no_log_group_configured_is_reported_not_guessed_at(self):
        self.m.cfg.log_group = ""
        out = self.logs()
        self.assertFalse(out["available"])
        self.assertEqual(out["events"], [])


class LogTailTests(unittest.TestCase):
    """logs() returns the newest events of the run, scoped to the run's window (#19)."""

    def test_tail_keeps_the_last_events_across_pages(self):
        pages = [{"events": [{"timestamp": i, "message": f"line {i}\n"} for i in range(0, 150)]},
                 {"events": [{"timestamp": i, "message": f"line {i}"} for i in range(150, 230)]},
                 {}]
        tail = renders._tail_events(pages, 100)
        self.assertEqual(len(tail), 100)
        self.assertEqual(tail[0], {"t": 130, "msg": "line 130", "stream": None})
        self.assertEqual(tail[-1], {"t": 229, "msg": "line 229", "stream": None})
        self.assertEqual(renders._tail_events([{"events": []}], 100), [])

    def test_the_stream_rides_along_because_sfr_lines_carry_no_shard_number(self):
        """`[sfr] 225/1132 done=...` says nothing about which shard produced it; only the
        stream it arrived on does, so shard_states() cannot fold without it."""
        pages = [{"events": [{"timestamp": 1, "message": "[sfr] x", "logStreamName": "s/0"},
                             {"timestamp": 2, "message": "[sfr] y", "logStreamName": "s/1"}]}]
        self.assertEqual([e["stream"] for e in renders._tail_events(pages, 10)], ["s/0", "s/1"])

    def test_query_window_follows_the_run(self):
        import datetime
        start = int(datetime.datetime(2026, 8, 24, tzinfo=datetime.timezone.utc).timestamp() * 1000)
        rec = run_record("r1", "running")
        q = renders._log_query(rec, "/aws/batch/x", now_ms=start + 60_000)
        self.assertEqual(q, {"logGroupName": "/aws/batch/x", "startTime": start})
        rec["finished_at"] = "2026-08-24T00:20:00Z"
        q = renders._log_query(rec, "/aws/batch/x")
        self.assertEqual(q["endTime"], start + 20 * 60_000 + renders.LOG_END_SLACK_MS)
        self.assertEqual(q["startTime"], start)      # a short run keeps its whole window

    def test_a_live_runs_window_does_not_grow_with_the_run(self):
        """Every open log box is re-read every 5 s, so the walk must not be O(run volume) —
        a long run (or one whose finished_at was never recorded) scanned all of it (#19)."""
        rec = run_record("r1", "running")            # submitted_at, no finished_at
        now = renders._ms("2026-08-24T00:00:00Z") + 6 * 3600 * 1000
        q = renders._log_query(rec, "/aws/batch/x", now_ms=now)
        self.assertEqual(q["startTime"], now - renders.TAIL_WINDOW_MS)
        self.assertNotIn("endTime", q)

    def test_a_finished_runs_window_is_a_tail_too(self):
        """logs() walks EVERY page of the window and keeps the last 100, so a finished
        multi-hour run was re-walked in full on each of the SPA's 5 s polls — for every
        open log box, on a sync route that pins a worker for the whole walk (#19)."""
        rec = run_record("r1", "failed", finished_at="2026-08-24T06:00:00Z")
        end = renders._ms("2026-08-24T06:00:00Z")
        q = renders._log_query(rec, "/aws/batch/x")
        self.assertEqual(q["startTime"], end - renders.TAIL_WINDOW_MS)
        self.assertEqual(q["endTime"], end + renders.LOG_END_SLACK_MS)
        self.assertGreater(q["startTime"], renders._ms(rec["submitted_at"]))


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
        renders._note_verdict(self.m.runs["r1"], "instance-type restore failed: boom")
        rec = self.m.finish("r1")
        self.assertTrue(rec["finisher"]["songs_json_published"])
        self.assertEqual(rec["finisher"]["error"], "instance-type restore failed: boom")

    def test_a_retry_clears_the_previous_finishers_own_error(self):
        # only the verdict is carried over ("Run finisher" is offered exactly while
        # songs_json_published is false, and it must be able to clear its own failure)
        self.release.set()
        self.m.runs["r1"]["finisher"] = {"ran_at": "2026-08-24T01:05:00Z",
                                         "songs_json_published": False,
                                         "error": "manifest --songs-json-only failed"}
        rec = self.m.finish("r1")
        self.assertTrue(rec["finisher"]["songs_json_published"])
        self.assertIsNone(rec["finisher"]["error"])

    def test_the_verdict_survives_a_finisher_that_failed_and_was_retried(self):
        """The retry is exactly when the operator needs the explanation, and reading the
        verdict back out of finisher.error lost it there: the failed finisher set ran_at,
        so the next finish() read the whole string as its own error and cleared it (#19)."""
        from sfadmin import publishops
        self.release.set()
        renders._note_verdict(self.m.runs["r1"], "Batch job job-1 is unknown to describe_jobs")
        with unittest.mock.patch.object(publishops, "rebuild_and_publish",
                                        side_effect=RuntimeError("manifest failed")):
            rec = self.m.finish("r1")                        # the watcher's own attempt
        self.assertEqual(rec["finisher"]["error"],
                         "Batch job job-1 is unknown to describe_jobs; manifest failed")
        rec = self.m.finish("r1")                            # the operator clicks "Run finisher"
        self.assertTrue(rec["finisher"]["songs_json_published"])
        self.assertEqual(rec["finisher"]["error"],
                         "Batch job job-1 is unknown to describe_jobs")

    def test_every_verdict_reaches_the_finisher_note(self):
        # _poll's and _sleep_fleet's notes both happen before finish(); neither may erase
        # the other, and finish() carries the pair over (#19)
        self.release.set()
        rec = self.m.runs["r1"]
        renders._note_verdict(rec, "Batch job job-1 is unknown to describe_jobs")
        renders._note_verdict(rec, "instance-type restore failed: boom")
        out = self.m.finish("r1")
        self.assertEqual(out["finisher"]["error"],
                         "Batch job job-1 is unknown to describe_jobs; "
                         "instance-type restore failed: boom")

    def test_a_legacy_record_keeps_a_verdict_written_before_the_field_existed(self):
        # records already in the bucket carry the verdict in finisher.error, with ran_at
        # still None — the only thing that ever proved it was a verdict
        self.release.set()
        legacy = self.m.runs["r1"]
        legacy.pop("verdict")
        legacy["finisher"]["error"] = "Batch job job-1 is unknown to describe_jobs"
        rec = self.m.finish("r1")
        self.assertEqual(rec["finisher"]["error"], "Batch job job-1 is unknown to describe_jobs")


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
    variants += [{"id": "sf2-twin", "engine": "fluidsynth", "publish": True,
                  "alias_of": "sf2-0", "facets": {"completeness": "full_gm"}}]
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

    def test_a_byte_identical_twin_is_not_counted(self):
        """plan_jobs does not test alias_of, but sfr.config.load_variants drops aliases
        before cli.py hands it the list — a twin renders once, under the canonical id. The
        catalog has none today, so only this test keeps the two selections in step (#19)."""
        counts, _ = planner.engine_availability(pathlib.Path(self.repo))
        self.assertEqual(counts["fluidsynth"], 4)          # sf2-0..3; sf2-twin is an alias
        self.assertEqual(self.m.plan(["a"], 1)["jobs"], 5)

    def test_engine_subset_shrinks_the_estimate(self):
        self.assertEqual(self.m.plan(["a", "b"], 1, engines=["adlmidi"])["jobs"], 2)
        self.assertEqual(self.m.plan(["a"], 1, engines=["fluidsynth"])["jobs"], 4)
        self.assertEqual(self.m.plan(["a"], 1, engines=["fluidsynth", "adlmidi"])["jobs"], 5)

    def test_limit_does_not_change_the_reported_variants_per_song(self):
        """--limit is a per-SHARD cap on the flat job list; each song still plans its whole
        matrix. Reporting it as the per-song count made the run record (knobs.variants,
        estimate.variants_per_song) claim "3 variants per song" for a run that plans 5 and
        truncates once per shard (#19)."""
        est = self.m.plan(["a", "b"], 1, limit=3)
        self.assertEqual(est["variants_per_song"], 5)     # the catalog's count, uncapped
        self.assertEqual(est["jobs"], 3)                  # ... and the cap still counts
        self.assertEqual(self.m.plan(["a"], 1, engines=["adlmidi"], limit=3)["variants_per_song"], 1)
        self.assertEqual(self.m.plan(["a"], 1, variants=40, limit=7)["variants_per_song"], 40)

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

    def test_a_submit_the_reconciler_gave_up_on_takes_its_record_back(self):
        """The backstop has headroom now, but if a tick ever does fail a submit that then
        succeeds, the job is real and the record must go back to live: a leftover
        finished_at puts the log window behind the run and empties its Logs box (#19)."""
        boto3 = unittest.mock.MagicMock()
        boto3.client.return_value.submit_job.return_value = {"jobId": "job-9"}
        self.m.cfg.job_queue, self.m.cfg.job_definition = "q", "jd"

        def stage(_self, _plan):   # patched onto the class: the reconciler ticks mid-stage
            with unittest.mock.patch.dict(sys.modules, {"boto3": boto3}):
                self.m._reconcile_once(now=time.time() + renders.SUBMIT_EXEMPT_S + 60)
            self.assertEqual(self.m.runs[next(iter(self.m.runs))]["state"], "failed")

        with unittest.mock.patch.dict(sys.modules, {"boto3": boto3}), \
                unittest.mock.patch.object(FakeManager, "_stage", stage):
            rec = self.m.submit(["a"], 1, 60.0)
        self.assertEqual(rec["state"], "running")
        self.assertEqual(rec["batch_job_id"], "job-9")
        self.assertIsNone(rec["finished_at"])
        self.assertIsNone(rec["verdict"])
        self.assertIsNone(rec["finisher"]["error"])
        self.assertNotIn("endTime", renders._log_query(rec, "/aws/batch/x"))

    def test_a_rom_engine_is_refused_with_the_real_reason(self):
        # sc55 is a real, pinned engine, but roms/ is empty on the fleet: "unknown engine"
        # sent the operator looking for a typo (#19)
        with self.assertRaises(ValueError) as cm:
            self.m.plan(["a"], 1, engines=["sc55"])
        self.assertIn("cannot run on the fleet", str(cm.exception))
        self.assertIn("roms/", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
