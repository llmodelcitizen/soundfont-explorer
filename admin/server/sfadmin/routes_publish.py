"""Published-renders API: overview, remove a track, S3-listing-based prune."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import bootstrapstate, publishops, publocks
from .config import get_config
from .renders import get_manager

router = APIRouter()


def _guard():
    if not bootstrapstate.ready():
        raise HTTPException(503, "still bootstrapping — try again shortly")
    if not get_config().site_bucket:
        raise HTTPException(503, "site bucket unknown (bundle.env)")


def _no_active_run() -> None:
    """UX guard: a live run's shards are publishing sets this rebuild would index halfway.
    It is check-then-act (the run can go terminal, or its finisher can start, right after
    this returns), so it is not what keeps two writers off songs.json — publocks is (#19).

    The phase comes back with the run from one snapshot: re-asking finishing() afterwards
    could miss a finisher that completed in between and answer "a render run is live" for a
    run that is terminal and done (#19)."""
    if not get_config().render_enabled:
        return
    phase = get_manager().active_phase()
    if not phase:
        return
    cur, is_finishing = phase
    if is_finishing:
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
            return publishops.remove_track(sid)   # republishes songs.json, then deletes a/ + s/
    except ValueError as e:
        raise HTTPException(400, str(e)) from e   # a malformed id is the caller's fault
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
        # publocks fences other requests; resync_and_publish holds OPS_LOCK across the pair
        with publocks.exclusive("POST /api/published/rebuild"):
            return publishops.resync_and_publish()
    except publocks.Busy as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(500, f"rebuild failed: {e}") from e
