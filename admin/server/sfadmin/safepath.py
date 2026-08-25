"""Containment for user-supplied relative paths (the SPA catch-all). Stdlib only, so the
unit tests run without the FastAPI stack.

Starlette hands a `{full_path:path}` parameter to the route without collapsing `..`
(percent-encoded forms arrive decoded), and os.path.join discards the root when the
second argument is absolute (`//etc/passwd`), so a bare join is an arbitrary file read for
anyone holding a session cookie. Every candidate is resolved (symlinks included) and must
stay under the resolved root.
"""
from __future__ import annotations

import os


def contained_file(root: str, rel: str) -> str | None:
    """Absolute path of the regular file `rel` under `root`, or None when there is no such
    file or the path escapes `root` by any route (`..`, absolute, symlink)."""
    if not rel:
        return None
    try:
        base = os.path.realpath(root)
        cand = os.path.realpath(os.path.join(base, rel.lstrip("/\\")))
        if os.path.commonpath([base, cand]) != base or cand == base:
            return None
        return cand if os.path.isfile(cand) else None
    except (ValueError, OSError):   # embedded NUL, unreadable component, ...
        return None
