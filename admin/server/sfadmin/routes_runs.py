"""Render-run API: plan (estimate), submit, watch, logs, terminate, finish."""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException

from . import bootstrapstate
from .config import get_config
from .renders import get_manager

router = APIRouter()

FINISH_ROUTE_WAIT_S = 15  # how long a manual "Run finisher" waits for the publish mutex


def _mgr():
    if not bootstrapstate.ready():
        raise HTTPException(503, "still bootstrapping — try again shortly")
    return get_manager()


@router.get("/api/render/songs")
def render_songs() -> dict:
    """What can be rendered: the build-side songs.json (curated + canon-ok imports)."""
    cfg = get_config()
    try:
        with open(os.path.join(cfg.repo, "songs", "songs.json")) as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise HTTPException(503, "no songs.json in the snapshot — run a canon check first") from None
    ids = {s["id"] for s in doc["songs"]}
    try:  # canon-ok library tracks missing here = the render list predates the last canon run
        from .library import get_library
        missing = sum(1 for e in get_library().entries()
                      if e["canon"]["status"] == "ok" and not e["hidden"] and e["id"] not in ids)
    except Exception:
        missing = 0
    return {"render_enabled": cfg.render_enabled, "missing_canon": missing,
            "songs": [{"id": s["id"], "title": s["title"], "path": s.get("path"),
                       "duration_s": s["duration_s"]} for s in doc["songs"]]}


def _variants(body: dict) -> int | None:
    """Optional override of the per-song job count; the default is derived from the
    catalog (planner.variant_counts), never a literal."""
    return int(body["variants"]) if body.get("variants") else None


@router.post("/api/render/plan")
def plan(body: dict) -> dict:
    try:
        return _mgr().plan(list(body.get("songs", [])), int(body.get("shards", 8)),
                           variants=_variants(body),
                           engines=body.get("engines") or None,
                           limit=int(body["limit"]) if body.get("limit") else None,
                           # priced under the knob the run will actually use: over-subscription
                           # overlaps per-job overhead, so it changes the bill (#45)
                           worker_factor=float(body["worker_factor"]) if body.get("worker_factor") else None)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _knobs(body: dict) -> dict:
    """The three knobs that change what the fleet can run, as the form sends them."""
    return {
        "shard_vcpus": int(body["shard_vcpus"]) if body.get("shard_vcpus") else None,
        "shard_memory_mib": int(body["shard_memory_mib"]) if body.get("shard_memory_mib") else None,
        "instance_types": body.get("instance_types") or None,
    }


@router.post("/api/render/capacity")
def capacity(body: dict) -> dict:
    """Can the account actually run this shape? Answered before submitting, not 40 minutes in."""
    try:
        return _mgr().capacity(int(body.get("shards", 8)), **_knobs(body))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        # a preflight that cannot run must not block the Estimate button
        return {"lines": [f"preflight unavailable: {e}"], "concurrent_shards": None,
                "waves": None, "ok": True, "degraded": False, "bad_pools": []}


@router.post("/api/runs")
def submit(body: dict) -> dict:
    m = _mgr()
    try:
        return m.submit(
            songs=list(body.get("songs", [])),
            shards=int(body.get("shards", 8)),
            max_usd=float(body.get("max_usd", 60.0)),
            engines=body.get("engines") or None,
            limit=int(body["limit"]) if body.get("limit") else None,
            instance_types=body.get("instance_types") or None,
            shard_vcpus=int(body["shard_vcpus"]) if body.get("shard_vcpus") else None,
            shard_memory_mib=int(body["shard_memory_mib"]) if body.get("shard_memory_mib") else None,
            worker_factor=float(body["worker_factor"]) if body.get("worker_factor") else None,
            variants=_variants(body),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/api/runs")
def list_runs() -> dict:
    return {"runs": _mgr().list()}


@router.get("/api/runs/{rid}")
def get_run(rid: str) -> dict:
    try:
        return _mgr().get(rid)
    except KeyError:
        raise HTTPException(404, f"no run {rid}") from None


@router.get("/api/runs/{rid}/logs")
def run_logs(rid: str, view: str = "signal", stream: str | None = None,
             limit: int = 200) -> dict:
    """`signal`: the filtered tail folded into per-shard progress. `raw`: one shard's
    unfiltered output, named by `stream` (which the signal view supplies)."""
    try:
        return _mgr().logs(rid, limit=max(1, min(limit, 2000)), view=view, stream=stream)
    except KeyError:
        raise HTTPException(404, f"no run {rid}") from None


@router.post("/api/runs/{rid}/terminate")
def terminate(rid: str) -> dict:
    try:
        return _mgr().terminate(rid)
    except KeyError:
        raise HTTPException(404, f"no run {rid}") from None


@router.post("/api/runs/{rid}/finish")
def finish(rid: str) -> dict:
    try:
        # a request thread must not sit on the publish mutex for the watcher's half hour:
        # if another writer holds it, finish() records that as the finisher's error and the
        # button is still there to retry
        return _mgr().finish(rid, lock_wait=FINISH_ROUTE_WAIT_S)
    except KeyError:
        raise HTTPException(404, f"no run {rid}") from None
    except RuntimeError as e:  # already being finished
        raise HTTPException(409, str(e)) from e
