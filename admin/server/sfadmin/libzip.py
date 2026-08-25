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
import zipfile
from typing import Iterable

_build_lock = threading.Lock()


def zip_path(cache: str, updated_at: str | None) -> str:
    stamp = hashlib.sha256((updated_at or "empty").encode()).hexdigest()[:16]
    return os.path.join(cache, f"library-{stamp}.zip")


def ensure_zip(cache: str, updated_at: str | None, files: Iterable[tuple[str, str]]) -> str:
    """Path of the zip for this library version, built from (src, arcname) pairs if it is
    not there yet. Missing sources are skipped (the local mirror can lag S3)."""
    out = zip_path(cache, updated_at)
    with _build_lock:
        if os.path.exists(out):
            return out
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
        for f in os.listdir(cache):  # drop stale versions, now that the new one is in place
            if f.startswith("library-") and f.endswith(".zip") and f != os.path.basename(out):
                os.remove(os.path.join(cache, f))
    return out
