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
STAGE_EXCLUDES = ["--exclude", "import/*", "--exclude", "*__pycache__/*", "--exclude", "*.pyc"]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        return next((r for r in self.list() if r["state"] not in TERMINAL), None)

    # ------------------------------------------------------------ plan + submit

    def plan(self, songs: list[str], shards: int, variants: int = 566) -> dict:
        planner = _planner()
        import pathlib
        durations = planner.song_durations(pathlib.Path(self.cfg.repo))
        unknown = [s for s in songs if s not in durations]
        if unknown:
            raise ValueError(f"not in songs.json (run canon first): {unknown[:5]}"
                             + ("…" if len(unknown) > 5 else ""))
        plan = planner.plan_shards(songs, shards, durations)
        cpu_h, usd = planner.estimate(plan, variants)
        return {"shards": plan, "jobs": len(songs) * variants,
                "cpu_h": round(cpu_h, 1), "usd": round(usd, 2)}

    def submit(self, songs: list[str], shards: int, max_usd: float,
               engines: list[str] | None = None, limit: int | None = None,
               instance_types: list[str] | None = None,
               shard_vcpus: int | None = None, shard_memory_mib: int | None = None,
               variants: int = 566) -> dict:
        if not self.cfg.render_enabled:
            raise RuntimeError("render fleet is not deployed (empty render config)")
        if self.active():
            raise RuntimeError(f"run {self.active()['run_id']} is still {self.active()['state']}")
        est = self.plan(songs, shards, variants)
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
                      "shard_memory_mib": shard_memory_mib, "variants": variants},
            "estimate": {k: est[k] for k in ("jobs", "cpu_h", "usd")},
            "batch_job_id": None, "submitted_at": _now(), "finished_at": None,
            "status_summary": {}, "published_sets": {},
            "instance_types_before": None, "instance_types_restored": True,
            "finisher": {"ran_at": None, "songs_json_published": False, "error": None},
        }
        with self.lock:
            self.runs[rid] = rec
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

    def _watch(self, rid: str) -> None:
        import boto3
        batch = boto3.client("batch")
        try:
            rec = self.get(rid)
            while rec["state"] not in TERMINAL:
                try:
                    j = batch.describe_jobs(jobs=[rec["batch_job_id"]])["jobs"][0]
                except Exception:  # transient API error or job not yet visible
                    time.sleep(POLL_S)
                    continue
                summary = j.get("arrayProperties", {}).get("statusSummary") or {j["status"]: 1}
                rec["status_summary"] = summary
                self._scan_published(rec)
                if j["status"] in ("SUCCEEDED", "FAILED"):
                    rec["state"] = "succeeded" if j["status"] == "SUCCEEDED" else "failed"
                    rec["finished_at"] = _now()
                self._put(rec)
                if rec["state"] in TERMINAL:
                    break
                time.sleep(POLL_S)
            self._sleep_fleet(rec)
            self.finish(rid)
        finally:
            with self.lock:
                self._watching.discard(rid)

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

    def _reconcile_once(self) -> None:
        import boto3
        live = False
        for rec in self.list():
            if rec["state"] in TERMINAL:
                continue
            if not rec["batch_job_id"]:
                # crashed between record write and submit-job; nothing is running
                age = time.time() - datetime.datetime.fromisoformat(
                    rec["submitted_at"].replace("Z", "+00:00")).timestamp()
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

    def finish(self, rid: str) -> dict:
        """Index what the shards published: rebuild + publish songs.json. Idempotent."""
        from . import publishops
        rec = self.get(rid)
        try:
            publishops.sync_down()
            publishops.rebuild_and_publish()
            rec["finisher"] = {"ran_at": _now(), "songs_json_published": True, "error": None}
        except Exception as e:
            rec["finisher"] = {"ran_at": _now(), "songs_json_published": False, "error": str(e)}
        self._put(rec)
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
        import boto3
        rec = self.get(rid)
        if not self.cfg.log_group:
            return []
        since = int(datetime.datetime.fromisoformat(
            rec["submitted_at"].replace("Z", "+00:00")).timestamp() * 1000)
        r = boto3.client("logs").filter_log_events(
            logGroupName=self.cfg.log_group, startTime=since, limit=limit)
        return [{"t": e["timestamp"], "msg": e["message"].rstrip()} for e in r.get("events", [])]


_manager: RunManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> RunManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = RunManager()
        return _manager
