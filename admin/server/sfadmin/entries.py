"""library.json entry pieces shared by the server and admin/scripts/ingest.py.

Stdlib only, and no imports from the rest of `sfadmin`: `admin/scripts/ingest.py` runs from
the repo root with only `admin/server` added to sys.path, and the `admin/server` unit tests
import this module the same way.
"""
from __future__ import annotations

MIDI_EXTS = {".mid", ".midi", ".rmi"}

#: The keys of an entry's `canon` block, in the order library.json carries them.
CANON_KEYS = ("status", "reason", "canonical_sha256", "duration_s", "checked_at")


def canon_state(status: str, reason: str | None = None, *, canonical_sha256: str | None = None,
                duration_s: float | None = None, checked_at: str | None = None) -> dict:
    """An entry's `canon` block: every key always present, always in CANON_KEYS order
    (library.json is read and diffed by eye, so the key order is part of the format)."""
    return {"status": status, "reason": reason, "canonical_sha256": canonical_sha256,
            "duration_s": duration_s, "checked_at": checked_at}
