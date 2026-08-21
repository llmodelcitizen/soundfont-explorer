"""Small helpers shared by the catalog tools (stdlib only)."""

from __future__ import annotations

import json
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINES_JSON = os.path.join(REPO_ROOT, "render", "engines.json")


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
