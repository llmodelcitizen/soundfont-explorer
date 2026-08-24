#!/usr/bin/env python3
"""Generate songs/corpus-imports.json from the admin library document.

The admin library (s3://<admin-bucket>/library/library.json, mirrored locally) is the source
of truth for every imported MIDI: its pinned id, current path, display name and metadata.
This tool translates it into a corpus fragment in corpus.json's spec shape; canon.py merges
the fragment over corpus.json's own import/FILES entries when the file exists (and behaves
exactly as before when it does not).

Fragment specs always pin "id" (so renames never orphan renders) and carry the directory in
"path" with the leaf name as "title" — canon passes both through to songs.json, where the
public client builds its folder tree from them. Hidden library entries are omitted, which is
how a track leaves the site without touching its renders.

    python3 songs/tools/fragment.py --library <library.json> [--out songs/corpus-imports.json]

Deterministic output (sorted by path, no timestamp): regenerating from the same library is
byte-identical, so "did anything change" is a file compare.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SONGS_DIR = os.path.dirname(HERE)
SCHEMA = 1


def build_fragment(library: dict) -> dict:
    songs = []
    for e in sorted(library["entries"].values(), key=lambda e: (e["path"], e["id"])):
        if e.get("hidden"):
            continue
        spec = {
            "id": e["id"],
            "src": "import/FILES/" + e["path"],
            "title": e["name"],
            "license": "owner-supplied",
        }
        parent = e["path"].rpartition("/")[0]
        if parent:
            spec["path"] = parent
        for k in ("composer", "sequencer", "source_url", "inject", "trim"):
            if e.get(k):
                spec[k] = e[k]
        songs.append(spec)
    return {"schema": SCHEMA, "generated_by": "songs/tools/fragment.py", "songs": songs}


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True, help="path to library.json")
    ap.add_argument("--out", default=os.path.join(SONGS_DIR, "corpus-imports.json"))
    args = ap.parse_args(argv)
    with open(args.library, encoding="utf-8") as fh:
        library = json.load(fh)
    frag = build_fragment(library)
    text = json.dumps(frag, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("%s: %d import specs (%d hidden entries omitted)"
          % (args.out, len(frag["songs"]), len(library["entries"]) - len(frag["songs"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
