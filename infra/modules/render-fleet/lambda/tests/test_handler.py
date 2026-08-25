"""Watchdog Lambda unit tests: stdlib only, boto3 is stubbed, nothing here touches AWS.

    python3 -m unittest discover -s infra/modules/render-fleet/lambda/tests -t infra/modules/render-fleet/lambda -v
"""
import datetime as dt
import os
import sys
import types
import unittest

# handler.py reads its configuration and builds its clients at import time; the clients are
# swapped for fakes per test, so boto3 itself only has to exist.
os.environ.update({"COMPUTE_ENV": "soundfont-explorer-render", "JOB_QUEUE": "soundfont-explorer-render",
                   "TOPIC_ARN": "arn:aws:sns:us-east-1:0:alerts",
                   "MAX_INSTANCE_MINUTES": "240", "MAX_JOB_MINUTES": "60"})
_boto3 = types.ModuleType("boto3")
_boto3.client = lambda name: None
sys.modules["boto3"] = _boto3

import handler  # noqa: E402

# handler() takes the wall clock itself, so fixture ages are measured from the real "now"; the
# limits (60 / 240 min) dwarf the few ms between here and the call.
NOW = dt.datetime.now(dt.timezone.utc)


def ms(minutes_ago):
    return int((NOW - dt.timedelta(minutes=minutes_ago)).timestamp() * 1000)


class FakeBatch:
    """The slice of the Batch API the watchdog uses, with the real ListJobs contract: a listing by
    jobQueue returns plain jobs and array *parents* only, children are listed by arrayJobId, and
    exactly one of the two selectors may be given. Pages are `page` entries long."""

    def __init__(self, jobs, page=100):
        self.jobs = {j["jobId"]: j for j in jobs}
        self.page = page
        self.terminated = []
        self.ce_state = "ENABLED"
        self.list_calls = []

    def list_jobs(self, **kw):
        self.list_calls.append({k: v for k, v in kw.items() if k != "nextToken"})
        assert ("jobQueue" in kw) != ("arrayJobId" in kw), kw
        if "arrayJobId" in kw:
            sel = [j for j in self.jobs.values() if j.get("arrayJobId") == kw["arrayJobId"]]
        else:
            sel = [j for j in self.jobs.values() if "arrayJobId" not in j]
        sel = [self._summary(j) for j in sel if j["status"] == kw.get("jobStatus", "RUNNING")]
        start = int(kw.get("nextToken") or 0)
        out = {"jobSummaryList": sel[start:start + self.page]}
        if start + self.page < len(sel):
            out["nextToken"] = str(start + self.page)
        return out

    @staticmethod
    def _summary(j):
        return {k: v for k, v in j.items()
                if k in ("jobId", "jobName", "status", "startedAt", "arrayProperties")}

    def describe_jobs(self, jobs):
        assert 0 < len(jobs) <= 100, jobs
        return {"jobs": [self.jobs[i] for i in jobs]}

    def terminate_job(self, jobId, reason):
        self.terminated.append(jobId)

    def describe_compute_environments(self, computeEnvironments):
        return {"computeEnvironments": [{"state": self.ce_state}]}

    def update_compute_environment(self, computeEnvironment, state):
        self.ce_state = state


class FakeEC2:
    def __init__(self, instances=()):
        self.instances = list(instances)
        self.terminated = []

    def describe_instances(self, Filters):
        return {"Reservations": [{"Instances": self.instances}]}

    def terminate_instances(self, InstanceIds):
        self.terminated += InstanceIds


class FakeSNS:
    def __init__(self):
        self.published = []

    def publish(self, **kw):
        self.published.append(kw)


def plain(job_id, status="RUNNING", minutes_ago=None):
    j = {"jobId": job_id, "jobName": "soundfont-explorer-smoke", "status": status}
    if minutes_ago is not None:
        j["startedAt"] = ms(minutes_ago)
    return j


def array_job(parent_id, children_minutes_ago, parent_status="PENDING"):
    """An array parent (never RUNNING, no startedAt) plus one RUNNING child per entry; None means
    the child has no startedAt yet."""
    parent = {"jobId": parent_id, "jobName": "soundfont-explorer-render", "status": parent_status,
              "arrayProperties": {"size": len(children_minutes_ago)}}
    kids = []
    for i, ago in enumerate(children_minutes_ago):
        k = {"jobId": f"{parent_id}:{i}", "jobName": "soundfont-explorer-render", "status": "RUNNING",
             "arrayJobId": parent_id}
        if ago is not None:
            k["startedAt"] = ms(ago)
        kids.append(k)
    return [parent] + kids


def stuck_ids(now=NOW):
    return sorted(j for j, _, _ in handler.stuck_jobs(now))


class StuckJobsTest(unittest.TestCase):
    def setUp(self):
        handler.ec2 = FakeEC2()
        handler.sns = FakeSNS()

    def batch(self, jobs, page=100):
        handler.batch = FakeBatch(jobs, page)
        return handler.batch

    def test_plain_running_job_over_limit(self):
        self.batch([plain("a", minutes_ago=90), plain("b", minutes_ago=5)])
        self.assertEqual(stuck_ids(), ["a"])

    def test_array_children_are_watched(self):
        # A real run is one array job: the parent sits in PENDING while its children run, and a
        # queue listing never shows the children, so the watchdog used to see nothing at all here.
        b = self.batch(array_job("p", [90, 5, None]))
        self.assertEqual(stuck_ids(), ["p:0"])
        self.assertIn({"arrayJobId": "p", "jobStatus": "RUNNING"}, b.list_calls)

    def test_array_parent_still_submitted_is_expanded_too(self):
        self.batch(array_job("p", [61], parent_status="SUBMITTED"))
        self.assertEqual(stuck_ids(), ["p:0"])

    def test_plain_waiting_jobs_are_not_expanded(self):
        b = self.batch([plain("x", status="PENDING"), plain("y", status="SUBMITTED")])
        self.assertEqual(stuck_ids(), [])
        self.assertEqual([c for c in b.list_calls if "arrayJobId" in c], [])

    def test_pagination_across_every_listing(self):
        jobs = [plain(f"j{i}", minutes_ago=70) for i in range(5)] + array_job("p", [70, 70, 70])
        self.batch(jobs, page=2)
        self.assertEqual(stuck_ids(), ["j0", "j1", "j2", "j3", "j4", "p:0", "p:1", "p:2"])


class HandlerTest(unittest.TestCase):
    def setUp(self):
        handler.ec2 = FakeEC2()
        handler.sns = FakeSNS()

    def test_stuck_child_trips_and_only_that_child_is_terminated(self):
        handler.batch = b = FakeBatch(array_job("p", [90, 5]))
        r = handler.handler({}, None)
        self.assertTrue(r["tripped"])
        self.assertEqual(b.terminated, ["p:0"])
        self.assertEqual(b.ce_state, "DISABLED")
        self.assertEqual(len(handler.sns.published), 1)
        self.assertIn("p:0", handler.sns.published[0]["Message"])

    def test_healthy_run_does_not_trip(self):
        handler.batch = b = FakeBatch(array_job("p", [5, 5]))
        self.assertEqual(handler.handler({}, None), {"ok": True, "tripped": False})
        self.assertEqual(b.terminated, [])
        self.assertEqual(b.ce_state, "ENABLED")
        self.assertEqual(handler.sns.published, [])

    def test_old_instance_trips_without_any_job(self):
        handler.batch = FakeBatch([])
        handler.ec2 = FakeEC2([{"InstanceId": "i-1", "InstanceType": "c7a.48xlarge",
                                "LaunchTime": NOW - dt.timedelta(minutes=300)}])
        r = handler.handler({}, None)
        self.assertTrue(r["tripped"])
        self.assertEqual(handler.ec2.terminated, ["i-1"])
        self.assertEqual(handler.batch.ce_state, "DISABLED")


if __name__ == "__main__":
    unittest.main()
