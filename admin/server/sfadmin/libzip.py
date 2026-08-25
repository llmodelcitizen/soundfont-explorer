"""The whole-library zip: built once per library version under the cache dir, then served
from disk (routes_library.library_zip).

One builder at a time, writing to a private temp name. Two first requests for the same
version used to open the same `<out>.tmp`, interleave their writes (a corrupt zip) and
race on the rename (the loser 500'd on a vanished temp file); stale versions were also
deleted before the replacement existed (#19).
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
import zipfile
from typing import Iterable

_build_lock = threading.Lock()
# ensure_zip() hands a path back and the route opens it a statement later, so a version
# that was current a moment ago may still be about to be opened: deleting it right then is
# the 500 this module exists to remove. Every hand-out touches the file, and a build only
# drops versions nobody has asked for in this long (#19).
KEEP_STALE_S = 300
# a build's private temp is removed by the finally below, but a process killed mid-build
# (an sfadmin restart during a zip build) leaves one behind for ever — it is not a
# library-*.zip, so the sweep never saw it. Well past any real build, so a temp another
# process is still writing (its mtime grows as it writes) is never taken (#19).
STALE_TMP_S = 3600


def zip_path(cache: str, updated_at: str | None) -> str:
    stamp = hashlib.sha256((updated_at or "empty").encode()).hexdigest()[:16]
    return os.path.join(cache, f"library-{stamp}.zip")


def ensure_zip(cache: str, updated_at: str | None, files: Iterable[tuple[str, str]]) -> str:
    """Path of the zip for this library version, built from (src, arcname) pairs if it is
    not there yet. Missing sources are skipped (the local mirror can lag S3)."""
    out = zip_path(cache, updated_at)
    with _build_lock:
        if os.path.exists(out):
            os.utime(out)  # "handed out just now": keeps a build in another thread off it
        else:
            tmp = f"{out}.{os.getpid()}-{threading.get_ident()}.tmp"
            try:
                with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                    for src, arcname in files:
                        if os.path.exists(src):
                            z.write(src, arcname)
                os.replace(tmp, out)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        # on every hand-out, not just on a build: sweeping only after a build left the
        # previous version's zip in the cache until the library next changed — for ever if
        # it stopped changing, which doubled the old one-zip ceiling (#19)
        _sweep(cache, os.path.basename(out))
    return out


def _sweep(cache: str, keep: str) -> None:
    """Drop stale versions (now that the new one is in place) and abandoned temps."""
    now = time.time()
    for f in os.listdir(cache):
        if not f.startswith("library-") or f == keep:
            continue
        age = STALE_TMP_S if f.endswith(".tmp") else KEEP_STALE_S if f.endswith(".zip") else None
        if age is None:
            continue
        p = os.path.join(cache, f)
        try:
            if now - os.path.getmtime(p) > age:
                os.remove(p)
        except OSError:      # vanished under us, or another process got there first
            pass
