"""Published-renders API: overview, remove a track, S3-listing-based prune."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import bootstrapstate, publishops
from .config import get_config
from .renders import get_manager

router = APIRouter()


def _guard():
    if not bootstrapstate.ready():
        raise HTTPException(503, "still bootstrapping — try again shortly")
    if not get_config().site_bucket:
        raise HTTPException(503, "site bucket unknown (bundle.env)")


def _no_active_run() -> None:
    if get_config().render_enabled and get_manager().active():
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
        return publishops.remove_track(sid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e   # a malformed id is the caller's fault
    except Exception as e:
        raise HTTPException(500, f"remove failed: {e}") from e


@router.post("/api/published/prune")
def prune(body: dict | None = None) -> dict:
    _guard()
    _no_active_run()
    dry = bool((body or {}).get("dry_run", True))
    try:
        return publishops.prune(dry_run=dry)
    except Exception as e:
        raise HTTPException(500, f"prune failed: {e}") from e


@router.post("/api/published/rebuild")
def rebuild() -> dict:
    """Sync sets down and republish songs.json — e.g. after a rename/hide + canon run."""
    _guard()
    _no_active_run()
    try:
        return publishops.resync_and_publish()   # holds OPS_LOCK across the pair
    except Exception as e:
        raise HTTPException(500, f"rebuild failed: {e}") from e
