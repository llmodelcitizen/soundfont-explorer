"""library.json entry pieces shared by the server and admin/scripts/ingest.py.

Stdlib only, and no imports from the rest of `sfadmin`: `admin/scripts/ingest.py` runs from
the repo root with only `admin/server` added to sys.path, and the `admin/server` unit tests
import this module the same way.
"""
from __future__ import annotations

MIDI_EXTS = {".mid", ".midi", ".rmi"}

#: The keys of an entry's `canon` block, in the order library.json carries them.
CANON_KEYS = ("status", "reason", "canonical_sha256", "duration_s", "checked_at")

#: Everything about an entry that is not identity (id/path/name/sha256/size), with the value
#: a file gets when nobody has said otherwise.  A library entry is the ONLY way a MIDI reaches
#: songs.json since songs/corpus.json and songs/private/ went away (2026-08-25), so every
#: field canon.py needs to publish a song has to be here — the publishing half (`license`
#: through `inject_note`) as much as the editorial half.  new_entry() is the one constructor:
#: the server's upload path, admin/scripts/ingest.py and the migration script all mint through
#: it, so a new field cannot reach two of the three and be forgotten by the last.
ENTRY_DEFAULTS: dict = {
    "composer": None,
    "sequencer": None,
    "source_url": None,
    "hidden": False,
    "inject": None,
    "trim": None,
    "notes": None,
    "license": "owner-supplied",     # a key into songs/licenses.json
    "license_fields": None,          # {mutopia_id}/{cc0_source}-style holes in its notice template
    "extra_include_classes": None,   # SoundFont completeness classes beyond the default set
    "inject_note": None,             # free text appended to the published `modifications`
}


def canon_state(status: str, reason: str | None = None, *, canonical_sha256: str | None = None,
                duration_s: float | None = None, checked_at: str | None = None) -> dict:
    """An entry's `canon` block: every key always present, always in CANON_KEYS order
    (library.json is read and diffed by eye, so the key order is part of the format)."""
    return {"status": status, "reason": reason, "canonical_sha256": canonical_sha256,
            "duration_s": duration_s, "checked_at": checked_at}


def new_entry(sid: str, path: str, name: str, *, sha256: str, size: int, added_at: str,
              canon: dict, **meta) -> dict:
    """One library.json entry. `meta` overrides ENTRY_DEFAULTS and may name nothing else."""
    unknown = sorted(set(meta) - set(ENTRY_DEFAULTS))
    if unknown:
        raise ValueError(f"not entry fields: {unknown}")
    return {"id": sid, "path": path, "name": name, "sha256": sha256, "size": size,
            **{k: meta.get(k, v) for k, v in ENTRY_DEFAULTS.items()},
            "added_at": added_at, "modified_at": added_at, "canon": canon}


def backfill(entry: dict) -> list[str]:
    """Give an entry written before a field existed that field's default, in place; return the
    keys added. library.json outlives every schema change it has ever had, so reads tolerate a
    missing key — but a document where every entry has every key is one the admin UI can show
    and edit uniformly."""
    added = [k for k in ENTRY_DEFAULTS if k not in entry]
    for k in added:
        entry[k] = ENTRY_DEFAULTS[k]
    return added
