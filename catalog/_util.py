"""Small helpers shared by the catalog tools (stdlib only)."""

from __future__ import annotations

import datetime as _dt
import json
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINES_JSON = os.path.join(REPO_ROOT, "render", "engines.json")


ISO_SECONDS = "%Y-%m-%dT%H:%M:%SZ"   # generated_at / *_at everywhere: UTC, whole seconds, literal Z


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def now_iso(now: _dt.datetime | None = None) -> str:
    """``now`` (a UTC-aware datetime, default: the current time) in the ISO_SECONDS format.

    The one place the catalog tools format timestamps; ``render/sfr/jobs.py`` and
    ``admin/server/sfadmin/clock.py`` carry the same helper for their own deployables
    (the render image and the admin bundle do not ship this package).
    """
    return (now or utc_now()).strftime(ISO_SECONDS)


def write_json(doc: dict, out: str) -> None:
    """Write ``doc`` atomically (tmp file + rename), 1-space indent, trailing newline."""
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, out)


def count_values(values) -> dict:
    """``{value: occurrences}`` sorted by value."""
    c: dict = {}
    for v in values:
        c[v] = c.get(v, 0) + 1
    return dict(sorted(c.items()))


def decade_of(year: int | None) -> str:
    return f"{year // 10 * 10}s" if year else "unknown"


def load_engines(path: str = ENGINES_JSON) -> dict:
    """``render/engines.json`` (the declared single source for engine versions and render constants)."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
