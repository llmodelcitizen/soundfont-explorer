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
import re
import secrets
import subprocess
import sys
import threading
import time

from .clock import now_iso
from .config import get_config

TERMINAL = ("succeeded", "failed", "terminated")
POLL_S = 30
# describe_jobs answers `jobs: []` for a job Batch no longer knows — it forgets jobs some
# days after they end, e.g. a run whose terminal state was never recorded because the
# server was down. Retrying that forever kept the run live and every submit/prune 409'd
# (#19). Ten CONSECUTIVE empties — ten calls with nine POLL_S sleeps between them, ~4.5 min
# — also covers describe_jobs lagging a fresh submit_job.
# An empty list is also the correct answer for a job id from ANOTHER region, so a box whose
# region moved would call a live run failed here. Not guarded: the region comes from one
# place (bundle.env, via config) for both submit and poll, and requiring an earlier
# successful poll would bring back the forever-live run — the first poll after a restart,
# which is exactly the case above, is the one that finds the job gone.
MISSING_JOB_POLLS = 10
# The same escape hatch for the other way describe_jobs can stop answering: revoked
# credentials, a job queue/region that no longer exists, sustained throttling. Retrying
# that forever leaves the run non-terminal, so active() stays truthy and every submit and
# publish 409s until someone finds the manual Terminate button (#19). Deliberately much
# larger than MISSING_JOB_POLLS (~30 min of CONSECUTIVE failures): unlike a forgotten job,
# an API error says nothing about the run, and calling a live run failed parks the fleet.
POLL_ERROR_POLLS = 60
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
# treated as crashed even if this process thinks its thread is alive. _stage runs TWO syncs,
# so a slow-but-healthy submit can legitimately reach 2 * STAGE_TIMEOUT_S; at exactly that
# the reconciler would fail a live submit, park the fleet under it and leave the record
# with a finished_at in the past (which empties the run's Logs box for ever) (#19).
SUBMIT_EXEMPT_S = 2 * STAGE_TIMEOUT_S + 600

# Batch applies a compute-environment change asynchronously and refuses a second one while it is
# in flight. Instance-type changes settle in seconds; the timeout is a backstop, not a target.
CE_SETTLE_TIMEOUT_S = 180.0
CE_SETTLE_POLL_S = 2.0
CE_RESTORE_ATTEMPTS = 3


def _ms(iso: str) -> int:
    return int(datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


LOG_END_SLACK_MS = 60_000  # a terminated shard still logs for a few seconds after finished_at
# logs() walks every page of the window and keeps the last 100, and the SPA re-reads every
# open log box every 5 s from a sync route (which pins an anyio worker for the whole
# uncancellable walk) — so the window has to stay O(1) in run length at BOTH ends. A live
# run's would otherwise grow with the run, and a finished multi-hour run was re-walked in
# full on every request. Half an hour of shard output is far more than the 100 shown (#19).
TAIL_WINDOW_MS = 30 * 60_000


def _log_query(rec: dict, log_group: str, now_ms: int | None = None) -> dict:
    """filter_log_events window for one run: the last TAIL_WINDOW_MS before now (still
    going) or before finished_at, never reaching back past submit — so a later run's shards
    never show up in this run's tail. The log group is shared by every run."""
    q = {"logGroupName": log_group}
    if rec.get("finished_at"):
        end = _ms(rec["finished_at"])
        q["endTime"] = end + LOG_END_SLACK_MS
    else:
        end = now_ms if now_ms is not None else int(time.time() * 1000)
    q["startTime"] = max(_ms(rec["submitted_at"]), end - TAIL_WINDOW_MS)
    return q


def _note_verdict(rec: dict, note: str) -> None:
    """Record why the run ended the way it did — a job Batch has forgotten, describe_jobs
    that stopped answering, a failed instance-type restore, a submit the reconciler gave up
    on. Kept in `verdict`, its own field, for two reasons (#19):

    * finish() has to be able to re-read it on every retry. Recovering it from
      finisher.error instead meant guessing, from `ran_at`, whether that string was a
      verdict or the last finisher's own failure — so the verdict was erased by exactly the
      retry it explains (finisher fails ⇒ ran_at set ⇒ the click that fixes it clears it).
    * notes accumulate. Assigning finisher.error wholesale meant a failed instance-type
      restore overwrote the poll's verdict before finish() ever read it.

    finisher.error mirrors it until the finisher runs, so the SPA (which shows only that
    field) says something as soon as the run ends."""
    notes = [n for n in (rec.get("verdict") or "").split("; ") if n]
    if note not in notes:
        notes.append(note)
    rec["verdict"] = "; ".join(notes)
    fin = rec.setdefault("finisher", {"ran_at": None, "songs_json_published": False,
                                      "error": None})
    if not fin.get("ran_at"):
        fin["error"] = rec["verdict"]


def _prior_verdict(rec: dict) -> str | None:
    """What finish() carries into finisher.error. Records written before `verdict` existed
    kept the verdict in finisher.error, and only an un-run finisher proves it is one."""
    fin = rec.get("finisher") or {}
    return rec.get("verdict") or (None if fin.get("ran_at") else fin.get("error")) or None


# ---------------------------------------------------------------- log tail

# Every line shard.py emits is prefixed "[shard N]" (its own log()) or "[sfr]" (the renderer's
# progress, forwarded unbuffered). s5cmd's per-object receipts are neither, and on the
# 2026-08-25 run they were 96.6% of a stream: 3,236 `cp s3://...` lines against four real ones,
# per shard, before a single variant was rendered. That is why the Logs box showed nothing
# worth reading — a 100-event tail of a 26,000-event window is pure staging noise.
#
# Filtering in CloudWatch rather than after the fetch is also what makes the route fast enough
# to poll: the same 30-minute window went from 25,993 events in 3.6 s to 113 in 1.15 s. The
# route is sync, so every one of those seconds pinned an anyio worker per open box (#19).
#
# Traceback/ERROR catch output that never reaches log() — a Python crash on stderr, an s5cmd
# failure — and cost nothing when nothing is broken.
SIGNAL_PATTERN = '?"[sfr]" ?"[shard" ?"Traceback" ?"ERROR" ?"Error"'

_PROGRESS = re.compile(
    r"\[sfr\]\s+\d+/(?P<total>\d+)\s+done=(?P<done>\d+)\s+failed=(?P<failed>\d+)"
    r"\s+skipped=(?P<skipped>\d+)\s+running=(?P<running>\d+)"
    r"(?:\s+mem_free=(?P<mem_free>\d+))?"
    r"\s+elapsed=(?P<elapsed>\d+)s\s+eta=(?P<eta>\d+)s")
_PLAN = re.compile(r"\[sfr\]\s+(?P<jobs>\d+) jobs, (?P<workers>\d+) workers")
_SHARD = re.compile(r"\[shard (?P<n>\d+)\]\s?(?P<rest>.*)", re.S)
_SONGS = re.compile(r"^(?P<n>\d+) songs: (?P<songs>.*?)\s+workers=(?P<workers>\d+)")
_STAGED = re.compile(r"^staged (?P<fonts>\d+) fonts \((?P<gib>[\d.]+) GiB\).*?(?P<secs>\d+)s")
_PUBLISHED = re.compile(r"^published (?P<song>\S+): (?P<variants>\d+) variants")
_RENDER_DONE = re.compile(r"^render finished rc=(?P<rc>-?\d+) in (?P<secs>\d+)s")
_PHASES = re.compile(r"^phases (?P<blob>\{.*\})", re.S)
_EXCLUDED = re.compile(r"^excluded (?P<n>\d+) variant\(s\): (?P<detail>.+)", re.S)


def _int(m, key: str):
    v = m.group(key)
    return int(v) if v is not None else None


def _blank_shard(stream: str) -> dict:
    return {"shard": None, "stream": stream, "phase": "starting", "songs": [],
            "done": None, "total": None, "failed": None, "skipped": None, "running": None,
            "workers": None, "eta_s": None, "elapsed_s": None, "mem_free": None,
            "staged_fonts": None, "staged_gib": None, "stage_s": None, "render_rc": None,
            "excluded": None, "excluded_detail": None,
            "render_s": None, "published": [], "problems": [], "phases": None,
            "updated_at": None, "last": None}


def shard_states(events: list[dict]) -> list[dict]:
    """Fold a filtered tail into one row per shard — the thing the Logs view actually wants.

    The raw lines cannot answer "how far along is shard 6" without reading them: progress is a
    line every 10 s whose successors make it obsolete, and the `[sfr]` lines carry no shard
    number at all (only the stream they arrived on identifies them). So the fold is keyed on
    logStreamName and the shard index is learned from whichever `[shard N]` line shares it.
    """
    by_stream: dict[str, dict] = {}
    for e in events:
        stream = e.get("stream") or ""
        st = by_stream.setdefault(stream, _blank_shard(stream))
        msg, ts = e["msg"], e["t"]
        if st["updated_at"] is None or ts >= st["updated_at"]:
            st["updated_at"], st["last"] = ts, msg
        m = _PROGRESS.search(msg)
        if m:
            st.update(total=_int(m, "total"), done=_int(m, "done"), failed=_int(m, "failed"),
                      skipped=_int(m, "skipped"), running=_int(m, "running"),
                      mem_free=_int(m, "mem_free"), elapsed_s=_int(m, "elapsed"),
                      eta_s=_int(m, "eta"))
            if st["phase"] in ("starting", "staging"):
                st["phase"] = "rendering"
            continue
        m = _PLAN.search(msg)
        if m:
            st["total"], st["workers"] = _int(m, "jobs"), _int(m, "workers")
            if st["phase"] in ("starting", "staging"):
                st["phase"] = "rendering"
            continue
        m = _SHARD.match(msg)
        if not m:
            if "Traceback" in msg or "ERROR" in msg:
                st["problems"].append(msg.strip()[:400])
            continue
        st["shard"], rest = int(m.group("n")), m.group("rest")
        if rest.startswith("!!"):
            st["problems"].append(rest.lstrip("! ").strip()[:400])
            continue
        sub = _SONGS.match(rest)
        if sub:
            st["songs"] = sub.group("songs").split()
            st["workers"] = int(sub.group("workers"))
            if st["phase"] == "starting":
                st["phase"] = "staging"
            continue
        sub = _STAGED.match(rest)
        if sub:
            st.update(staged_fonts=int(sub.group("fonts")), staged_gib=float(sub.group("gib")),
                      stage_s=int(sub.group("secs")))
            if st["phase"] in ("starting", "staging"):
                st["phase"] = "rendering"
            continue
        sub = _PUBLISHED.match(rest)
        if sub:
            st["published"].append({"song": sub.group("song"),
                                    "variants": int(sub.group("variants"))})
            continue
        sub = _EXCLUDED.match(rest)
        if sub:
            # excluded variants are an outcome, not a fault: silent fonts and #27's peak
            # ceiling. Shown because "550 of 566 published" is otherwise unexplained.
            st["excluded"] = int(sub.group("n"))
            st["excluded_detail"] = sub.group("detail").strip()
            continue
        sub = _RENDER_DONE.match(rest)
        if sub:
            st["render_rc"], st["render_s"] = int(sub.group("rc")), int(sub.group("secs"))
            st["phase"] = "publishing"
            continue
        sub = _PHASES.match(rest)
        if sub:
            try:
                st["phases"] = json.loads(sub.group("blob"))
            except ValueError:
                pass
            continue
        if rest.startswith("shard complete"):
            st["phase"] = "done"
    out = list(by_stream.values())
    # unknown shard indexes sort last but stay visible: a stream that has only ever emitted
    # `[sfr]` progress is a real shard doing real work, just not one that has said which
    out.sort(key=lambda s: (s["shard"] is None, s["shard"] if s["shard"] is not None else 0))
    return out


def _tail_events(pages, limit: int) -> list[dict]:
    """The LAST `limit` events of a filter_log_events page sequence. The API only walks
    forward and its `limit=` truncates from the head, so one capped call returned the
    oldest events of the run, not a tail (#19)."""
    keep: collections.deque = collections.deque(maxlen=limit)
    for page in pages:
        for e in page.get("events", []):
            keep.append({"t": e["timestamp"], "msg": e["message"].rstrip(),
                         "stream": e.get("logStreamName")})
    return list(keep)


def _preflight():
    """render/cloud/preflight.py, loaded the same lazy way as the planner: it ships in the
    bundle but is not on sys.path until something asks for it."""
    repo = get_config().repo
    p = os.path.join(repo, "render", "cloud")
    if p not in sys.path:
        sys.path.insert(0, p)
    import preflight  # noqa: PLC0415
    return preflight


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

    def active_phase(self) -> tuple[dict, bool] | None:
        """The run holding the single-writer resources and whether it is in its finisher: a
        live one, or a terminal one whose finisher is still rebuilding songs.json — a new
        submit, a Published-tab remove/prune or rebuild must wait for that too, or two
        writers race on songs.json (#19). Both facts come from one snapshot; asking
        active() and then finishing() could see a finisher that completed in between and
        report "a render run is live" for a run that is terminal and done."""
        with self.lock:  # one snapshot: a finish() starting between the two reads was missed
            finishing = set(self._finishing)
            rec = next((r for r in self.list()
                        if r["state"] not in TERMINAL or r["run_id"] in finishing), None)
            return None if rec is None else (rec, rec["run_id"] in finishing)

    def active(self) -> dict | None:
        phase = self.active_phase()
        return phase[0] if phase else None

    def finishing(self, rid: str) -> bool:
        with self.lock:
            return rid in self._finishing

    # ------------------------------------------------------------ plan + submit

    def plan(self, songs: list[str], shards: int, variants: int | None = None,
             engines: list[str] | None = None, limit: int | None = None) -> dict:
        """Shards + cost estimate under the run's knobs. The per-song job count comes from
        catalog/variants.json narrowed by `engines`; `variants` is an explicit override of
        that count (the old --variants knob). `limit` does NOT narrow it — it is a
        per-SHARD cap the fleet applies to the shard's flat job list, so folding it in here
        made the record claim "5 variants per song" for a run that still plans all of
        them (#19). It reaches the counts through job_count/estimate instead."""
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
        per_song = planner.variants_per_song(counts, engines, None, unavailable)
        if variants:
            per_song = variants
        # --limit is per shard, not per song: the job count is summed over the shards (#19)
        cpu_h, usd = planner.estimate(plan, per_song, limit)
        return {"shards": plan, "jobs": planner.job_count(plan, per_song, limit),
                "variants_per_song": per_song,
                "cpu_h": round(cpu_h, 1), "usd": round(usd, 2)}

    def capacity(self, shards: int, *, shard_vcpus: int | None = None,
                 shard_memory_mib: int | None = None,
                 instance_types: list[str] | None = None) -> dict:
        """What the fleet can actually run under THESE knobs, before anything is submitted.

        Batch accepts an array the account cannot run: maxvCpus and the regional Spot vCPU
        quota are different limits, and Auto Scaling then retries MaxSpotInstanceCountExceeded
        in silence while the array sits RUNNABLE (#25). The CLI has printed this since #25; the
        Renders tab could not, so the UI let you submit a shape that was guaranteed to run in
        four sequential waves (#42)."""
        pf = _preflight()
        cap = pf.gather(self.cfg.compute_env, self.cfg.job_definition, shards,
                        shard_vcpus=shard_vcpus, shard_memory_mib=shard_memory_mib,
                        instance_types=instance_types)
        return {
            "lines": cap.report(),
            "concurrent_shards": cap.concurrent_shards,
            "waves": cap.waves,
            "planned_vcpus": cap.planned_vcpus,
            "quota_vcpus": cap.quota_vcpus,
            "headroom_vcpus": cap.headroom_vcpus,
            "ok": cap.ok,
            "degraded": cap.degraded,
            "bad_pools": pf.unusable_pools_for({"compute_environment": self.cfg.compute_env}),
            "quota_code": pf.SPOT_VCPU_QUOTA_CODE,
        }

    def submit(self, songs: list[str], shards: int, max_usd: float,
               engines: list[str] | None = None, limit: int | None = None,
               instance_types: list[str] | None = None,
               shard_vcpus: int | None = None, shard_memory_mib: int | None = None,
               variants: int | None = None) -> dict:
        if not self.cfg.render_enabled:
            raise RuntimeError("render fleet is not deployed (empty render config)")
        cur = self.active_phase()
        if cur:
            rec_, is_finishing = cur
            phase = "finishing (indexing what it published)" if is_finishing else rec_["state"]
            raise RuntimeError(f"run {rec_['run_id']} is still {phase}")
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
            "batch_job_id": None, "submitted_at": now_iso(), "finished_at": None,
            "status_summary": {}, "published_sets": {},
            "instance_types_before": None, "instance_types_restored": True,
            "verdict": None,
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
                self._wait_ce_settled(batch)      # a CE mid-update refuses the next change
                batch.update_compute_environment(
                    computeEnvironment=self.cfg.compute_env,
                    computeResources={"instanceTypes": instance_types})
                self._put(rec)
                self._wait_ce_settled(batch)      # ... including the state change below
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
            # the reconciler may have declared this submit wedged while it was staging
            # (SUBMIT_EXEMPT_S). The job is real, so the record goes back to live: a
            # leftover finished_at puts the run's log window in the past and empties its
            # Logs box for ever, and the leftover verdict describes a run that did start.
            rec["finished_at"] = None
            rec["verdict"] = None
            rec["finisher"]["error"] = None
            self._put(rec)
        except Exception as e:
            rec["state"] = "failed"
            _note_verdict(rec, f"submit failed: {e}")
            rec["finished_at"] = now_iso()
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
            # a timeout, so a wedged sync fails the submit instead of leaving the run
            # "submitting" for ever (which exempts it from the crash heuristic) (#19)
            subprocess.run(["aws", "s3", "sync", os.path.join(cfg.repo, sub),
                            f"s3://{cfg.fonts_bucket}/{sub}/", "--delete",
                            "--only-show-errors", *STAGE_EXCLUDES],
                           check=True, timeout=STAGE_TIMEOUT_S)

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
        Batch has stopped knowing the job at all (MISSING_JOB_POLLS consecutive empty
        answers) or describe_jobs has stopped answering (POLL_ERROR_POLLS)."""
        missing = errors = 0
        while rec["state"] not in TERMINAL:
            try:
                jobs = batch.describe_jobs(jobs=[rec["batch_job_id"]])["jobs"]
            except Exception as e:  # transient API error
                errors += 1
                # both counters are CONSECUTIVE runs, so an error must clear `missing`:
                # without this, empties spread over hours by intermittent throttling added
                # up to the grace count and failed a live run (#19)
                missing = 0
                if errors < POLL_ERROR_POLLS:
                    sleep(POLL_S)
                    continue
                rec["state"] = "failed"
                rec["status_summary"] = {"UNKNOWN": 1}
                _note_verdict(rec, f"describe_jobs failed {errors} polls in a row ({e}); "
                                   "the job may still be running — check Batch")
                rec["finished_at"] = now_iso()
                self._put(rec)
                return
            errors = 0
            if not jobs:
                missing += 1
                if missing < MISSING_JOB_POLLS:
                    sleep(POLL_S)
                    continue
                rec["state"] = "failed"
                rec["status_summary"] = {"UNKNOWN": 1}
                _note_verdict(rec, f"Batch job {rec['batch_job_id']} is unknown to "
                                   f"describe_jobs ({missing} polls): expired or never ran; "
                                   "outcome unknown")
                rec["finished_at"] = now_iso()
                self._put(rec)
                return
            missing = 0
            j = jobs[0]
            summary = j.get("arrayProperties", {}).get("statusSummary") or {j["status"]: 1}
            rec["status_summary"] = summary
            self._scan_published(rec)
            if j["status"] in ("SUCCEEDED", "FAILED"):
                rec["state"] = "succeeded" if j["status"] == "SUCCEEDED" else "failed"
                rec["finished_at"] = now_iso()
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

    def _wait_ce_settled(self, batch, timeout_s: float = CE_SETTLE_TIMEOUT_S, sleep=time.sleep) -> str:
        """Block until the compute environment leaves UPDATING/CREATING.

        Batch serialises changes to a compute environment: a second update_compute_environment
        while the first is still applying is refused outright with `ClientException: Cannot
        update, compute environment ... is being modified`. The submit path issues two in a row
        (instance types, then state=ENABLED), so without this the instance-type override could
        never work — and the restore in _sleep_fleet hit the same wall, leaving the fleet on the
        run's types (#42). Returns the final status; the caller decides what to do about it.
        """
        deadline = time.monotonic() + timeout_s
        status = "UNKNOWN"
        while time.monotonic() < deadline:
            try:
                ce = batch.describe_compute_environments(
                    computeEnvironments=[self.cfg.compute_env])["computeEnvironments"][0]
            except Exception:
                sleep(CE_SETTLE_POLL_S)
                continue
            status = ce.get("status", "UNKNOWN")
            if status not in ("UPDATING", "CREATING"):
                return status
            sleep(CE_SETTLE_POLL_S)
        return status

    def _sleep_fleet(self, rec: dict) -> None:
        """DISABLE the CE and restore Terraform's instance types if this run changed them."""
        import boto3
        batch = boto3.client("batch")
        try:
            if rec.get("instance_types_before") and not rec.get("instance_types_restored"):
                # the failure path runs straight after a refused update, so the CE is usually
                # still settling; restoring is the one step that must not be skipped (#42)
                last = None
                for _ in range(CE_RESTORE_ATTEMPTS):
                    self._wait_ce_settled(batch)
                    try:
                        batch.update_compute_environment(
                            computeEnvironment=self.cfg.compute_env,
                            computeResources={"instanceTypes": rec["instance_types_before"]})
                        rec["instance_types_restored"] = True
                        self._put(rec)
                        break
                    except Exception as e:      # noqa: PERF203 — retried deliberately
                        last = e
                if not rec.get("instance_types_restored") and last is not None:
                    raise last
        except Exception as e:
            # appended, not assigned: this runs one line after _poll, and overwriting the
            # dict dropped the poll's verdict before finish() could carry it over (#19)
            _note_verdict(rec, f"instance-type restore failed: {e}")
            self._put(rec)
        try:
            self._wait_ce_settled(batch)
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
                age = (now if now is not None else time.time()) - _ms(rec["submitted_at"]) / 1000
                if rec["run_id"] in submitting and age <= SUBMIT_EXEMPT_S:
                    # submit() is still staging in this process; it may already have ENABLED
                    # the CE, so this counts as live (a crash empties the set: restart case).
                    # The exemption is bounded: _stage's sync has a timeout, so a submit
                    # still in flight an hour on is wedged, and letting it keep the run
                    # "live" would block every later submit and park no compute (#19).
                    live = True
                    continue
                # crashed between record write and submit-job; nothing is running
                if age > 300:
                    rec["state"] = "failed"
                    _note_verdict(rec, f"submit was still staging after {int(age)}s — wedged"
                                  if rec["run_id"] in submitting else
                                  "no Batch job was ever submitted (crash during submit)")
                    rec["finished_at"] = now_iso()
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
            # the verdict (a job Batch forgot, a failed instance-type restore, …) is the
            # operator's only explanation of the run, and replacing the whole dict here
            # dropped it (#19). A previous finisher's own error is NOT carried over: a
            # retry that works clears it, and only it.
            prior = _prior_verdict(rec)
            try:
                with publocks.exclusive(f"finisher for run {rid}", timeout=lock_wait):
                    publishops.sync_down()
                    publishops.rebuild_and_publish()
                rec["finisher"] = {"ran_at": now_iso(), "songs_json_published": True, "error": prior}
            except Exception as e:
                rec["finisher"] = {"ran_at": now_iso(), "songs_json_published": False,
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
        rec["finished_at"] = now_iso()
        self._put(rec)
        self._sleep_fleet(rec)
        return rec

    def logs(self, rid: str, limit: int = 200, view: str = "signal",
             stream: str | None = None) -> dict:
        """What the run is doing, as a per-shard fold plus the tail it was folded from.

        `signal` (the default) filters in CloudWatch and folds the result into one row per
        shard. `raw` is the unfiltered output of ONE shard, scoped by `stream`: unfiltered
        AND unscoped is the 26,000-event walk this whole change exists to avoid, so asking
        for it without naming a stream gets the signal view instead.
        """
        import boto3
        rec = self.get(rid)
        if not self.cfg.log_group:
            return {"events": [], "shards": [], "view": view, "available": False}
        q = _log_query(rec, self.cfg.log_group)
        if view == "raw" and stream:
            q["logStreamNames"] = [stream]
        else:
            view, q["filterPattern"] = "signal", SIGNAL_PATTERN
        pages = boto3.client("logs").get_paginator("filter_log_events").paginate(
            **q, PaginationConfig={"PageSize": 10_000})
        events = _tail_events(pages, limit)
        return {"events": events, "view": view, "available": True,
                "shards": shard_states(events) if view == "signal" else []}


_manager: RunManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> RunManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = RunManager()
        return _manager
