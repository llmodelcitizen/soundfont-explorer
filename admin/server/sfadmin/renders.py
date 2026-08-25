"""Render runs on the burst fleet: submit, watch, reconcile, finish.

Ports render/cloud/submit.py's semantics into a long-lived service and closes its gaps:
every run gets a persistent record at s3://<admin>/runs/<run_id>.json (written BEFORE
submit-job, so no run can be lost), a watcher thread updates it as Batch progresses, and a
reconciler re-adopts live runs after a server restart and enforces the standing invariant:
**no live run ⇒ compute environment DISABLED** (the watchdog Lambda stays the backstop).

After any terminal state the finisher indexes what the shards actually published — syncs
/s and /c from the site bucket, runs `sfr manifest --songs-json-only`, uploads the new
catalog doc + songs.json, invalidates /songs.json — which closes the old "nothing writes
songs.json after a cloud run" gap. Even a FAILED run published whole songs on the way.

One run at a time: the staging area (fonts bucket songs/ + catalog/ + shards.json) and
songs.json publishing are both single-writer.
"""
from __future__ import annotations

import collections
import datetime
import json
import os
import secrets
import subprocess
import sys
import threading
import time

from .config import get_config

TERMINAL = ("succeeded", "failed", "terminated")
POLL_S = 30
# describe_jobs answers `jobs: []` for a job Batch no longer knows — it forgets jobs some
# days after they end, e.g. a run whose terminal state was never recorded because the
# server was down. Retrying that forever kept the run live and every submit/prune 409'd
# (#19). Ten polls (5 min) also covers describe_jobs lagging a fresh submit_job.
MISSING_JOB_POLLS = 10
STAGE_EXCLUDES = ["--exclude", "import/*", "--exclude", "*__pycache__/*", "--exclude", "*.pyc"]
# how long the background finisher waits for the publish mutex before recording an error:
# long enough to sit out a Published-tab rebuild, short enough that a wedged writer cannot
# hold a watcher thread for ever
FINISH_LOCK_WAIT_S = 1800
# staging is MIDI + JSON (tens of MB); an `aws s3 sync` still running after this is wedged,
# and letting it run forever kept the run "submitting" — which exempts it from the crash
# heuristic and blocks every later submit (#19)
STAGE_TIMEOUT_S = 1800
# backstop for that exemption: after this a submit still holding a record with no job id is
# treated as crashed even if this process thinks its thread is alive
SUBMIT_EXEMPT_S = 3600


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms(iso: str) -> int:
    return int(datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


LOG_END_SLACK_MS = 60_000  # a terminated shard still logs for a few seconds after finished_at


def _log_query(rec: dict, log_group: str) -> dict:
    """filter_log_events window for one run: from submit to (once finished) shortly after
    the end, so a later run's shards never show up in this run's tail. The log group is
    shared by every run."""
    q = {"logGroupName": log_group, "startTime": _ms(rec["submitted_at"])}
    if rec.get("finished_at"):
        q["endTime"] = _ms(rec["finished_at"]) + LOG_END_SLACK_MS
    return q


def _tail_events(pages, limit: int) -> list[dict]:
    """The LAST `limit` events of a filter_log_events page sequence. The API only walks
    forward and its `limit=` truncates from the head, so one capped call returned the
    oldest events of the run, not a tail (#19)."""
    keep: collections.deque = collections.deque(maxlen=limit)
    for page in pages:
        for e in page.get("events", []):
            keep.append({"t": e["timestamp"], "msg": e["message"].rstrip()})
    return list(keep)


def _planner():
    repo = get_config().repo
    p = os.path.join(repo, "render", "cloud")
    if p not in sys.path:
        sys.path.insert(0, p)
    import planner  # noqa: PLC0415
    return planner


class RunManager:
    def __init__(self) -> None:
        self.cfg = get_config()
        self.lock = threading.RLock()
        self.runs: dict[str, dict] = {}
        self._watching: set[str] = set()
        self._submitting: set[str] = set()     # runs whose submit() is in flight in this process
        self._finishing: set[str] = set()      # runs whose finisher is rebuilding songs.json
        self._loaded = False
        threading.Thread(target=self._reconcile_loop, name="run-reconciler", daemon=True).start()

    # ------------------------------------------------------------ persistence

    def _load_all(self) -> None:
        s3 = self.cfg.s3
        keys = []
        pages = s3.get_paginator("list_objects_v2").paginate(Bucket=self.cfg.bucket, Prefix="runs/")
        for page in pages:
            keys += [o["Key"] for o in page.get("Contents", [])]
        for key in keys:
            rid = key[len("runs/"):-len(".json")]
            if rid not in self.runs:
                try:
                    self.runs[rid] = json.load(s3.get_object(Bucket=self.cfg.bucket, Key=key)["Body"])
                except Exception:
                    pass
        self._loaded = True

    def _put(self, rec: dict) -> None:
        body = (json.dumps(rec, indent=1, sort_keys=True) + "\n").encode()
        self.cfg.s3.put_object(Bucket=self.cfg.bucket, Key=f"runs/{rec['run_id']}.json",
                               Body=body, ContentType="application/json")

    # ------------------------------------------------------------ reads

    def list(self) -> list[dict]:
        with self.lock:
            if not self._loaded:
                self._load_all()
            return sorted(self.runs.values(), key=lambda r: r["submitted_at"] or "", reverse=True)

    def get(self, rid: str) -> dict:
        with self.lock:
            if not self._loaded:
                self._load_all()
            if rid not in self.runs:
                raise KeyError(rid)
            return self.runs[rid]

    def active(self) -> dict | None:
        """The run holding the single-writer resources: a live one, or a terminal one whose
        finisher is still rebuilding songs.json — a new submit, a Published-tab remove/prune
        or rebuild must wait for that too, or two writers race on songs.json (#19)."""
        with self.lock:  # one snapshot: a finish() starting between the two reads was missed
            finishing = set(self._finishing)
            return next((r for r in self.list()
                         if r["state"] not in TERMINAL or r["run_id"] in finishing), None)

    def finishing(self, rid: str) -> bool:
        with self.lock:
            return rid in self._finishing

    # ------------------------------------------------------------ plan + submit

    def plan(self, songs: list[str], shards: int, variants: int | None = None,
             engines: list[str] | None = None, limit: int | None = None) -> dict:
        """Shards + cost estimate under the run's knobs. The per-song job count comes from
        catalog/variants.json narrowed by `engines` and capped by `limit`; `variants` is
        an explicit override of that count (the old --variants knob)."""
        planner = _planner()
        import pathlib
        repo = pathlib.Path(self.cfg.repo)
        durations = planner.song_durations(repo)
        unknown = [s for s in songs if s not in durations]
        if unknown:
            raise ValueError(f"not in songs.json (run canon first): {unknown[:5]}"
                             + ("…" if len(unknown) > 5 else ""))
        plan = planner.plan_shards(songs, shards, durations)
        counts, unavailable = planner.engine_availability(repo)
        per_song = planner.variants_per_song(counts, engines, limit, unavailable)
        if variants:
            per_song = min(variants, limit) if limit else variants
        # --limit is per shard, not per song: the job count is summed over the shards (#19)
        cpu_h, usd = planner.estimate(plan, per_song, limit)
        return {"shards": plan, "jobs": planner.job_count(plan, per_song, limit),
                "variants_per_song": per_song,
                "cpu_h": round(cpu_h, 1), "usd": round(usd, 2)}

    def submit(self, songs: list[str], shards: int, max_usd: float,
               engines: list[str] | None = None, limit: int | None = None,
               instance_types: list[str] | None = None,
               shard_vcpus: int | None = None, shard_memory_mib: int | None = None,
               variants: int | None = None) -> dict:
        if not self.cfg.render_enabled:
            raise RuntimeError("render fleet is not deployed (empty render config)")
        cur = self.active()
        if cur:
            phase = "finishing (indexing what it published)" if cur["state"] in TERMINAL else cur["state"]
            raise RuntimeError(f"run {cur['run_id']} is still {phase}")
        est = self.plan(songs, shards, variants, engines, limit)
        if est["usd"] > max_usd:
            raise ValueError(f"estimate ${est['usd']:.2f} exceeds max ${max_usd:.2f} — raise it deliberately")
        plan = est["shards"]
        if engines or limit:
            for s in plan:
                if engines:
                    s["engines"] = engines
                if limit:
                    s["limit"] = limit
        rid = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") \
            + "-" + secrets.token_hex(2)
        rec = {
            "schema": 1, "run_id": rid, "state": "staged",
            "songs": songs, "shards": plan,
            "knobs": {"shards": shards, "max_usd": max_usd, "engines": engines, "limit": limit,
                      "instance_types": instance_types, "shard_vcpus": shard_vcpus,
                      "shard_memory_mib": shard_memory_mib, "variants": est["variants_per_song"]},
            "estimate": {k: est[k] for k in ("jobs", "cpu_h", "usd", "variants_per_song")},
            "batch_job_id": None, "submitted_at": _now(), "finished_at": None,
            "status_summary": {}, "published_sets": {},
            "instance_types_before": None, "instance_types_restored": True,
            "finisher": {"ran_at": None, "songs_json_published": False, "error": None},
        }
        with self.lock:
            self.runs[rid] = rec
            # the reconciler's "crashed during submit" heuristic must not fire on a slow
            # _stage(): this thread is the submit, and it can take minutes (#19)
            self._submitting.add(rid)
            self._put(rec)  # before anything can go wrong: the run is never untracked
        try:
            import boto3
            self._stage(plan)
            batch = boto3.client("batch")
            if instance_types:
                ce = batch.describe_compute_environments(
                    computeEnvironments=[self.cfg.compute_env])["computeEnvironments"][0]
                rec["instance_types_before"] = ce["computeResources"].get("instanceTypes")
                rec["instance_types_restored"] = False
                batch.update_compute_environment(
                    computeEnvironment=self.cfg.compute_env,
                    computeResources={"instanceTypes": instance_types})
                self._put(rec)
            batch.update_compute_environment(computeEnvironment=self.cfg.compute_env, state="ENABLED")
            kwargs: dict = {"jobName": "soundfont-explorer-render", "jobQueue": self.cfg.job_queue,
                            "jobDefinition": self.cfg.job_definition}
            if len(plan) > 1:
                kwargs["arrayProperties"] = {"size": len(plan)}
            overrides = []
            if shard_vcpus:
                overrides.append({"type": "VCPU", "value": str(shard_vcpus)})
            if shard_memory_mib:
                overrides.append({"type": "MEMORY", "value": str(shard_memory_mib)})
            if overrides:
                kwargs["containerOverrides"] = {"resourceRequirements": overrides}
            rec["batch_job_id"] = batch.submit_job(**kwargs)["jobId"]
            rec["state"] = "running"
            self._put(rec)
        except Exception as e:
            rec["state"] = "failed"
            rec["finisher"]["error"] = f"submit failed: {e}"
            rec["finished_at"] = _now()
            self._put(rec)
            self._sleep_fleet(rec)
            raise
        finally:
            with self.lock:
                self._submitting.discard(rid)
        self._watch_async(rid)
        return rec

    def _stage(self, plan: list[dict]) -> None:
        cfg = self.cfg
        cfg.s3.put_object(Bucket=cfg.fonts_bucket, Key="shards.json",
                          Body=json.dumps(plan, indent=1).encode(), ContentType="application/json")
        for sub in ("songs", "catalog"):
            subprocess.run(["aws", "s3", "sync", os.path.join(cfg.repo, sub),
                            f"s3://{cfg.fonts_bucket}/{sub}/", "--delete",
                            "--only-show-errors", *STAGE_EXCLUDES], check=True)

    # ------------------------------------------------------------ watch + reconcile

    def _watch_async(self, rid: str) -> None:
        with self.lock:
            if rid in self._watching:
                return
            self._watching.add(rid)
        threading.Thread(target=self._watch, args=(rid,), name=f"run-{rid}", daemon=True).start()

    def _watch(self, rid: str, batch=None) -> None:
        """Poll to a terminal state, park the fleet, index what was published. `batch` is
        injectable so a test can drive the whole sequence — the finisher's effect on the
        record only shows when finish() runs after _poll, as it does here (#19)."""
        try:
            rec = self.get(rid)
            if batch is None:
                import boto3
                batch = boto3.client("batch")
            self._poll(rec, batch)
            self._sleep_fleet(rec)
            try:
                self.finish(rid)
            except RuntimeError:
                pass  # a manual "Run finisher" is already indexing this run
        finally:
            with self.lock:
                self._watching.discard(rid)

    def _poll(self, rec: dict, batch, sleep=time.sleep) -> None:
        """Follow the Batch job until the run is terminal — Batch says so, terminate() did,
        or Batch has stopped knowing the job at all (MISSING_JOB_POLLS)."""
        missing = 0
        while rec["state"] not in TERMINAL:
            try:
                jobs = batch.describe_jobs(jobs=[rec["batch_job_id"]])["jobs"]
            except Exception:  # transient API error
                sleep(POLL_S)
                continue
            if not jobs:
                missing += 1
                if missing < MISSING_JOB_POLLS:
                    sleep(POLL_S)
                    continue
                rec["state"] = "failed"
                rec["status_summary"] = {"UNKNOWN": 1}
                rec["finisher"]["error"] = (f"Batch job {rec['batch_job_id']} is unknown to "
                                            f"describe_jobs ({missing} polls): expired or never ran; "
                                            "outcome unknown")
                rec["finished_at"] = _now()
                self._put(rec)
                return
            missing = 0
            j = jobs[0]
            summary = j.get("arrayProperties", {}).get("statusSummary") or {j["status"]: 1}
            rec["status_summary"] = summary
            self._scan_published(rec)
            if j["status"] in ("SUCCEEDED", "FAILED"):
                rec["state"] = "succeeded" if j["status"] == "SUCCEEDED" else "failed"
                rec["finished_at"] = _now()
            self._put(rec)
            if rec["state"] in TERMINAL:
                return
            sleep(POLL_S)

    def _scan_published(self, rec: dict) -> None:
        """One paginated list over /s/ marks songs whose set doc landed after submit."""
        want = {s for s in rec["songs"] if s not in rec["published_sets"]}
        if not want:
            return
        since = datetime.datetime.fromisoformat(rec["submitted_at"].replace("Z", "+00:00"))
        pages = self.cfg.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.cfg.site_bucket, Prefix="s/")
        for page in pages:
            for o in page.get("Contents", []):
                parts = o["Key"].split("/")  # s/<song>/<hash>.json
                if len(parts) == 3 and parts[1] in want and o["LastModified"] >= since:
                    rec["published_sets"][parts[1]] = o["Key"]

    def _sleep_fleet(self, rec: dict) -> None:
        """DISABLE the CE and restore Terraform's instance types if this run changed them."""
        import boto3
        batch = boto3.client("batch")
        try:
            if rec.get("instance_types_before") and not rec.get("instance_types_restored"):
                batch.update_compute_environment(
                    computeEnvironment=self.cfg.compute_env,
                    computeResources={"instanceTypes": rec["instance_types_before"]})
                rec["instance_types_restored"] = True
                self._put(rec)
        except Exception as e:
            rec["finisher"]["error"] = f"instance-type restore failed: {e}"
            self._put(rec)
        try:
            batch.update_compute_environment(computeEnvironment=self.cfg.compute_env, state="DISABLED")
        except Exception:
            pass  # the reconciler and the watchdog both retry this

    def _reconcile_loop(self) -> None:
        from . import bootstrapstate
        while True:
            time.sleep(60)
            try:
                if not bootstrapstate.ready() or not self.cfg.render_enabled:
                    continue
                self._reconcile_once()
            except Exception:
                pass  # next tick

    def _reconcile_once(self, now: float | None = None) -> None:
        live = False
        with self.lock:
            submitting = set(self._submitting)
        for rec in self.list():
            if rec["state"] in TERMINAL:
                continue
            if not rec["batch_job_id"]:
                if rec["run_id"] in submitting:
                    # submit() is still staging in this process; it may already have ENABLED
                    # the CE, so this counts as live (a crash empties the set: restart case)
                    live = True
                    continue
                # crashed between record write and submit-job; nothing is running
                age = (now if now is not None else time.time()) - _ms(rec["submitted_at"]) / 1000
                if age > 300:
                    rec["state"] = "failed"
                    rec["finisher"]["error"] = "no Batch job was ever submitted (crash during submit)"
                    rec["finished_at"] = _now()
                    self._put(rec)
                    self._sleep_fleet(rec)
                continue
            live = True
            self._watch_async(rec["run_id"])  # re-adopt after restart (no-op when attached)
        if not live:
            import boto3
            batch = boto3.client("batch")
            try:
                ce = batch.describe_compute_environments(
                    computeEnvironments=[self.cfg.compute_env])["computeEnvironments"][0]
                if ce["state"] == "ENABLED":
                    batch.update_compute_environment(
                        computeEnvironment=self.cfg.compute_env, state="DISABLED")
            except Exception:
                pass

    # ------------------------------------------------------------ finisher

    def finish(self, rid: str, lock_wait: float = FINISH_LOCK_WAIT_S) -> dict:
        """Index what the shards published: rebuild + publish songs.json. Idempotent, never
        concurrent: the watcher's finish, a click on "Run finisher" and the Published tab's
        remove/prune/rebuild used to rewrite out/public/songs.json and the S3 key side by
        side (#19). The cross-writer mutex is publocks — it guards those files, not this
        method; _finishing only keeps a second finish for the SAME run out and tells the UI
        the run is not done. A second call for a run being finished raises RuntimeError."""
        from . import publishops, publocks
        rec = self.get(rid)
        with self.lock:
            if rid in self._finishing:
                raise RuntimeError(f"the finisher for run {rid} is already running")
            self._finishing.add(rid)
        try:
            # a verdict recorded before the finisher ran (a job Batch forgot, a failed
            # instance-type restore) is the operator's only explanation of the run, and
            # replacing the whole dict here dropped it (#19)
            prior = (rec.get("finisher") or {}).get("error")
            try:
                with publocks.exclusive(f"finisher for run {rid}", timeout=lock_wait):
                    publishops.sync_down()
                    publishops.rebuild_and_publish()
                rec["finisher"] = {"ran_at": _now(), "songs_json_published": True, "error": prior}
            except Exception as e:
                rec["finisher"] = {"ran_at": _now(), "songs_json_published": False,
                                   "error": "; ".join(x for x in (prior, str(e)) if x)}
            self._put(rec)
        finally:
            with self.lock:
                self._finishing.discard(rid)
        return rec

    # ------------------------------------------------------------ control

    def terminate(self, rid: str) -> dict:
        import boto3
        rec = self.get(rid)
        if rec["state"] in TERMINAL:
            return rec
        if rec["batch_job_id"]:
            boto3.client("batch").terminate_job(jobId=rec["batch_job_id"],
                                                reason="admin UI terminate")
        rec["state"] = "terminated"
        rec["finished_at"] = _now()
        self._put(rec)
        self._sleep_fleet(rec)
        return rec

    def logs(self, rid: str, limit: int = 100) -> list[dict]:
        """The last `limit` log events of the run (shard logs share one CloudWatch group)."""
        import boto3
        rec = self.get(rid)
        if not self.cfg.log_group:
            return []
        pages = boto3.client("logs").get_paginator("filter_log_events").paginate(
            **_log_query(rec, self.cfg.log_group), PaginationConfig={"PageSize": 10_000})
        return _tail_events(pages, limit)


_manager: RunManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> RunManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = RunManager()
        return _manager
