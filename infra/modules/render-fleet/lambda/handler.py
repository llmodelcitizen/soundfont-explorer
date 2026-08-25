"""Render-fleet watchdog: nothing expensive may run unattended.

Runs every 5 minutes (EventBridge). Three independent backstops, strongest first:

1. INSTANCE AGE — terminate any EC2 instance tagged project=soundfont-explorer-render older than
   MAX_INSTANCE_MINUTES. This is unconditional and does not care what Batch believes; a
   c7a.48xlarge is ~$3.5/h, so an instance nobody is watching is the whole risk.
2. STUCK JOB — terminate Batch jobs RUNNING longer than MAX_JOB_MINUTES, array children
   included (a run is one array job; see running_job_ids). The longest real job measured is
   402 s, so anything near an hour is wedged.
3. DISABLE — on any trip, set the compute environment to DISABLED so Batch cannot replace
   what we just killed, and e-mail. Re-enabling is manual, by design (docs/RENDER.md).

Everything is tag-scoped: the IAM policy only permits terminating instances tagged
project=soundfont-explorer-render, because this account is shared with unrelated projects.
"""
import datetime as dt
import os

import boto3

CE = os.environ["COMPUTE_ENV"]
QUEUE = os.environ["JOB_QUEUE"]
TOPIC = os.environ["TOPIC_ARN"]
TAG_KEY, TAG_VALUE = "project", os.environ.get("PROJECT_TAG", "soundfont-explorer-render")
MAX_INSTANCE_MIN = float(os.environ.get("MAX_INSTANCE_MINUTES", "240"))
MAX_JOB_MIN = float(os.environ.get("MAX_JOB_MINUTES", "60"))

ec2 = boto3.client("ec2")
batch = boto3.client("batch")
sns = boto3.client("sns")


def _age_min(t: dt.datetime, now: dt.datetime) -> float:
    return (now - t).total_seconds() / 60.0


def overage_instances(now):
    r = ec2.describe_instances(Filters=[
        {"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]},
        {"Name": "instance-state-name", "Values": ["pending", "running"]},
    ])
    out = []
    for res in r.get("Reservations", []):
        for i in res.get("Instances", []):
            age = _age_min(i["LaunchTime"], now)
            if age > MAX_INSTANCE_MIN:
                out.append((i["InstanceId"], i.get("InstanceType", "?"), age))
    return out


# An array parent never enters RUNNING: it sits in SUBMITTED, then PENDING, while its children
# run, and goes straight to SUCCEEDED/FAILED. ListJobs by queue returns plain jobs and parents
# only, so the children — every job a real run consists of — are reachable only by arrayJobId.
ARRAY_PARENT_STATES = ("SUBMITTED", "PENDING")


def _list_jobs(**kw):
    """Every jobSummaryList entry for one ListJobs selector, across pages."""
    out = []
    token = None
    while True:
        page = batch.list_jobs(**kw, **({"nextToken": token} if token else {}))
        out += page.get("jobSummaryList", [])
        token = page.get("nextToken")
        if not token:
            return out


def running_job_ids():
    """RUNNING job ids in the queue, array children included (see ARRAY_PARENT_STATES)."""
    ids = [j["jobId"] for j in _list_jobs(jobQueue=QUEUE, jobStatus="RUNNING")]
    for state in ARRAY_PARENT_STATES:
        for parent in _list_jobs(jobQueue=QUEUE, jobStatus=state):
            if "arrayProperties" in parent:
                kids = _list_jobs(arrayJobId=parent["jobId"], jobStatus="RUNNING")
                ids += [j["jobId"] for j in kids]
    return list(dict.fromkeys(ids))


def stuck_jobs(now):
    out = []
    ids = running_job_ids()
    for k in range(0, len(ids), 100):
        for j in batch.describe_jobs(jobs=ids[k:k + 100]).get("jobs", []):
            started = j.get("startedAt")
            if not started:
                continue
            age = _age_min(dt.datetime.fromtimestamp(started / 1000, dt.timezone.utc), now)
            if age > MAX_JOB_MIN:
                out.append((j["jobId"], j.get("jobName", "?"), age))
    return out


def disable_compute_env():
    state = batch.describe_compute_environments(computeEnvironments=[CE])["computeEnvironments"]
    if state and state[0].get("state") == "DISABLED":
        return False
    batch.update_compute_environment(computeEnvironment=CE, state="DISABLED")
    return True


def handler(event, context):
    now = dt.datetime.now(dt.timezone.utc)
    old = overage_instances(now)
    stuck = stuck_jobs(now)
    if not old and not stuck:
        return {"ok": True, "tripped": False}

    lines = []
    for jid, name, age in stuck:
        batch.terminate_job(jobId=jid, reason=f"watchdog: running {age:.0f} min > {MAX_JOB_MIN:.0f}")
        lines.append(f"terminated job {name} ({jid}) after {age:.0f} min")
    if old:
        ec2.terminate_instances(InstanceIds=[i for i, _, _ in old])
        lines += [f"terminated {t} {i} after {a:.0f} min" for i, t, a in old]
    if disable_compute_env():
        lines.append(f"compute environment {CE} set to DISABLED (re-enable manually)")

    msg = ("The Soundfont Explorer render fleet watchdog tripped.\n\n" + "\n".join(f"  - {x}" for x in lines) +
           f"\n\nLimits: instance {MAX_INSTANCE_MIN:.0f} min, job {MAX_JOB_MIN:.0f} min."
           "\nSee docs/RENDER.md 'Cloud runs'.\n")
    sns.publish(TopicArn=TOPIC, Subject="[Soundfont Explorer] render fleet watchdog tripped", Message=msg)
    return {"ok": True, "tripped": True, "actions": lines}
