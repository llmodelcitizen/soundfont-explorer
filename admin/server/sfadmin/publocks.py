"""The publish mutex: `out/public` and the site bucket's songs.json have one writer at a time.

Writers: the run finisher (renders.RunManager.finish) and the Published tab's
remove/prune/rebuild (routes_publish). Every one of them runs publishops.sync_down() —
`aws s3 sync --delete` into out/public/s and /c — and then rebuild_and_publish(), which
rewrites out/public/songs.json and copies it to s3://<site>/songs.json. Two at once
interleave one's write with the other's read and upload: a truncated songs.json on the
CDN, prune deleting audio a finisher has just published, remove_track's drop-guard tripping
on a set the other writer added.

The mutex used to live inside finish() alone, so the routes never took it and the
finish-vs-rebuild race stayed open through check-then-act: a route passes RunManager's
"no active run" check, and only afterwards does the watcher enter finish() (#19). Guarding
the resource instead of one of its writers closes that window; RunManager._finishing stays
as UX ("this run is still indexing"), not as the guard.

Callers never nest: this is a plain Lock, and a second take from the same thread deadlocks
just as it would from another one.
"""
from __future__ import annotations

import contextlib
import threading
import time

_lock = threading.Lock()
_state = threading.Lock()
_holder: tuple[str, float] | None = None


class Busy(RuntimeError):
    """Another writer holds the publish mutex; the caller should refuse, not queue."""

    def __init__(self, holder: str, since: float) -> None:
        self.holder = holder
        self.since = since
        super().__init__(f"another publish is in progress ({holder}, "
                         f"{int(max(0.0, time.time() - since))}s ago) — try again when it finishes")


def held_by() -> tuple[str, float] | None:
    """(what, started_at) of the current writer, or None. For status/diagnostics only —
    a decision made on this is a check-then-act race; take the lock instead."""
    with _state:
        return _holder


@contextlib.contextmanager
def exclusive(what: str, timeout: float = 0.0):
    """Hold the publish mutex for the duration of the block.

    `timeout` 0 (the default) refuses immediately with Busy — what an HTTP route wants, so
    an operator gets a 409 naming the holder instead of a request that hangs for minutes.
    A positive timeout waits that long; the background finisher uses one so a click on
    "Republish songs.json" a second earlier only delays it.
    """
    global _holder
    if not _lock.acquire(timeout=timeout if timeout > 0 else 0):
        cur = held_by()
        raise Busy(*(cur or ("another writer", time.time())))
    with _state:
        _holder = (what, time.time())
    try:
        yield
    finally:
        with _state:
            _holder = None
        _lock.release()
