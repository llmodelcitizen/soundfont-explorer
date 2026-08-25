"""Published-renders API: overview, remove a track, S3-listing-based prune."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import bootstrapstate, publishops, publocks
from .config import get_config
from .renders import TERMINAL, get_manager

router = APIRouter()


def _guard():
    if not bootstrapstate.ready():
        raise HTTPException(503, "still bootstrapping — try again shortly")
    if not get_config().site_bucket:
        raise HTTPException(503, "site bucket unknown (bundle.env)")


def _no_active_run() -> None:
    """UX guard: a live run's shards are publishing sets this rebuild would index halfway.
    It is check-then-act (the run can go terminal, or its finisher can start, right after
    this returns), so it is not what keeps two writers off songs.json — publocks is (#19)."""
    if not get_config().render_enabled:
        return
    cur = get_manager().active()
    if not cur:
        return
    if cur["state"] in TERMINAL and get_manager().finishing(cur["run_id"]):
        raise HTTPException(409, f"run {cur['run_id']} is finishing — it is indexing what it "
                                 "published; try again in a moment")
    raise HTTPException(409, "a render run is live — shards are publishing; try after it finishes")


@router.get("/api/published")
def published() -> dict:
    _guard()
    return publishops.overview()


@router.delete("/api/published/{sid}")
def remove(sid: str) -> dict:
    _guard()
    _no_active_run()
    try:
        with publocks.exclusive("DELETE /api/published/{sid}"):
            return publishops.remove_track(sid)   # deletes a/ + s/, then republishes songs.json
    except publocks.Busy as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"remove failed: {e}") from e


@router.post("/api/published/prune")
def prune(body: dict | None = None) -> dict:
    _guard()
    _no_active_run()
    dry = bool((body or {}).get("dry_run", True))
    try:
        # even a dry run reads the live songs.json a finisher may be halfway through
        # replacing; a real one deletes everything that read did not reference
        with publocks.exclusive("POST /api/published/prune"):
            return publishops.prune(dry_run=dry)
    except publocks.Busy as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"prune failed: {e}") from e


@router.post("/api/published/rebuild")
def rebuild() -> dict:
    """Sync sets down and republish songs.json — e.g. after a rename/hide + canon run."""
    _guard()
    _no_active_run()
    try:
        with publocks.exclusive("POST /api/published/rebuild"):
            publishops.sync_down()
            return publishops.rebuild_and_publish()
    except publocks.Busy as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"rebuild failed: {e}") from e
