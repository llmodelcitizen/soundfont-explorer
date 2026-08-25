#!/usr/bin/env python3
"""One-time library ingest: songs/import/FILES -> the admin bucket.

Builds library.json (the admin data model: one entry per MIDI file, keyed by a song id
minted exactly the way canon.py derives ids today, then PINNED — renames and moves never
change it, so existing renders under work/renders/<id>/ and the published /a/<id>/ and
/s/<id>/ prefixes survive reorganisation), mirrors the MIDI files to
s3://<admin-bucket>/library/FILES/, and uploads the document.

Only MIDI-ish files (.mid/.midi/.rmi, any case) are ingested; the NSF/FRM/mp3 remnants in
the import tree stay local. Metadata for files that already have corpus.json entries
(composer, sequencer, inject, trim) is carried over, and the minted ids are cross-checked
against songs/songs.json before anything is uploaded: an id mismatch would orphan renders,
so it is a hard error.

    admin/scripts/ingest.py --dry-run     # census, collisions, id cross-check; no AWS calls
    admin/scripts/ingest.py               # stage + upload (refuses if library.json exists)
    admin/scripts/ingest.py --force       # re-upload over an existing library.json

Needs: aws CLI authenticated; infra/live/outputs.json with the `admin` output.
Re-running without --force is safe: it refuses rather than re-minting ids over the truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "songs", "tools"))
sys.path.insert(0, os.path.join(ROOT, "admin", "server"))
import canon  # noqa: E402
import smf as S  # noqa: E402
from sfadmin.clock import now_iso  # noqa: E402

FILES = os.path.join(ROOT, "songs", "import", "FILES")
STAGE = os.path.join(ROOT, "work", "library-stage")
MIDI_EXTS = {".mid", ".midi", ".rmi"}
SCHEMA = 1


def admin_bucket() -> str:
    out = os.path.join(ROOT, "infra", "live", "outputs.json")
    try:
        admin = json.load(open(out))["admin"]["value"]
        return admin["bucket"]
    except (OSError, KeyError, TypeError):
        raise SystemExit(f"no admin outputs in {out} — apply with -var enable_admin=true and refresh it")


def corpus_import_specs() -> dict:
    """src (relative to songs/) -> corpus spec, for entries sourced from import/FILES."""
    corpus = json.load(open(os.path.join(ROOT, "songs", "corpus.json")))
    return {s["src"]: s for s in corpus["songs"] if s.get("src", "").startswith("import/FILES/")}


def published_import_ids() -> dict:
    """src -> pinned id from the current build-side songs.json (the ids renders are keyed by)."""
    doc = json.load(open(os.path.join(ROOT, "songs", "songs.json")))
    return {e["src"]: e["id"] for e in doc["songs"] if (e.get("src") or "").startswith("import/FILES/")}


def walk_midis() -> list:
    """Sorted relative paths (posix) of every MIDI-ish file under songs/import/FILES."""
    rels = []
    for dirpath, dirnames, filenames in os.walk(FILES):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.startswith("._"):  # AppleDouble resource forks, not MIDI
                continue
            if os.path.splitext(fn)[1].lower() in MIDI_EXTS:
                full = os.path.join(dirpath, fn)
                rels.append(os.path.relpath(full, FILES).replace(os.sep, "/"))
    return sorted(rels)


def mint(rel: str, spec: dict | None, taken: set) -> tuple:
    """(id, name, canon_status, canon_reason) for one file, exactly today's derivation.

    canon.import_labels() gives slug(f"{parent}/{name}") where name is the explicit corpus
    title, the SMF track-0 sequence title, or the filename stem. Files smf.py cannot parse
    fall back to the stem and are marked unparsed. Collisions get a deterministic -2/-3
    suffix in sorted-path order, recorded here and never recomputed.
    """
    src = "import/FILES/" + rel
    explicit = (spec or {}).get("title")
    status, reason = "pending", None
    try:
        with open(os.path.join(FILES, rel), "rb") as fh:
            parsed = S.parse(fh.read())
        sid, title, _ = canon.import_labels(src, parsed, explicit)
    except Exception as e:  # wild files: RIFF-wrapped .rmi, truncated SMF, ...
        status, reason = "unparsed", f"{type(e).__name__}: {e}"
        stem = re.sub(r"\.midi?$", "", rel, flags=re.I)  # same strip as import_labels
        parent, _, leaf = stem.rpartition("/")
        name = explicit or leaf
        title = f"{parent}/{name}" if parent else name
        sid = canon.slug(title)
    base, n = sid, 2
    while sid in taken:
        sid = f"{base}-{n}"
        n += 1
    name = title.rpartition("/")[2]
    return sid, name, status, reason, sid != base


def build_library() -> tuple:
    specs = corpus_import_specs()
    entries: dict = {}
    dup_sha: dict = {}
    suffixed: list = []
    ts = now_iso()
    for rel in walk_midis():
        src = "import/FILES/" + rel
        spec = specs.get(src)
        with open(os.path.join(FILES, rel), "rb") as fh:
            blob = fh.read()
        sha = hashlib.sha256(blob).hexdigest()
        sid, name, status, reason, was_suffixed = mint(rel, spec, set(entries))
        if was_suffixed:
            suffixed.append(sid)
        entries[sid] = {
            "id": sid,
            "path": rel,
            "name": name,
            "sha256": sha,
            "size": len(blob),
            "composer": (spec or {}).get("composer"),
            "sequencer": (spec or {}).get("sequencer"),
            "source_url": (spec or {}).get("source_url"),
            "hidden": False,
            "inject": (spec or {}).get("inject"),
            "trim": (spec or {}).get("trim"),
            "notes": None,
            "added_at": ts,
            "modified_at": ts,
            "canon": {"status": status, "reason": reason,
                      "canonical_sha256": None, "duration_s": None, "checked_at": None},
        }
        dup_sha.setdefault(sha, []).append(sid)
    doc = {"schema": SCHEMA, "updated_at": ts, "entries": entries}
    dups = {h: ids for h, ids in dup_sha.items() if len(ids) > 1}
    return doc, dups, suffixed


def crosscheck(doc: dict) -> list:
    """Every import already in songs.json must have minted the identical id at its path."""
    by_path = {e["path"]: e for e in doc["entries"].values()}
    problems = []
    for src, want in sorted(published_import_ids().items()):
        rel = src[len("import/FILES/"):]
        got = by_path.get(rel)
        if got is None:
            problems.append(f"{src}: in songs.json but not ingested")
        elif got["id"] != want:
            problems.append(f"{src}: minted id {got['id']!r} != pinned {want!r} (would orphan renders)")
    return problems


def stage_and_upload(doc: dict, bucket: str, force: bool) -> None:
    probe = subprocess.run(["aws", "s3api", "head-object", "--bucket", bucket,
                            "--key", "library/library.json"], capture_output=True)
    if probe.returncode == 0 and not force:
        raise SystemExit(f"s3://{bucket}/library/library.json already exists — it is the truth now; "
                         "re-run with --force only to rebuild it from scratch")
    # Hardlink the MIDI subset into a staging tree and sync that: `aws s3 sync --include`
    # patterns are case-sensitive (the .SF2 lesson, commit 9e91223); a staged tree can't
    # miss what the census counted.
    subprocess.run(["rm", "-rf", STAGE], check=True)
    for e in doc["entries"].values():
        dst = os.path.join(STAGE, "FILES", e["path"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.link(os.path.join(FILES, e["path"]), dst)
    with open(os.path.join(STAGE, "library.json"), "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    subprocess.run(["aws", "s3", "sync", os.path.join(STAGE, "FILES") + "/",
                    f"s3://{bucket}/library/FILES/", "--size-only"], check=True)
    subprocess.run(["aws", "s3", "cp", os.path.join(STAGE, "library.json"),
                    f"s3://{bucket}/library/library.json",
                    "--content-type", "application/json"], check=True)


def verify(doc: dict, bucket: str) -> None:
    # the CLI applies --query to each pagination page, so length(Contents) prints one
    # count per 1000-key page ("1000\n1000\n800") — sum them ("None" = an empty page)
    out = subprocess.run(["aws", "s3api", "list-objects-v2", "--bucket", bucket,
                          "--prefix", "library/FILES/", "--query", "length(Contents)",
                          "--output", "text"], capture_output=True, text=True, check=True).stdout
    n = sum(int(x) for x in out.split() if x != "None")
    want = len(doc["entries"])
    if n != want:
        raise SystemExit(f"verify FAILED: {n} objects under library/FILES/, expected {want}")
    print(f"verified: {n} objects in s3://{bucket}/library/FILES/ == {want} entries")


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--write-local", metavar="PATH",
                    help="write library.json to PATH and stop (no AWS calls); for fragment.py "
                         "runs and validation before the admin bucket exists")
    args = ap.parse_args(argv)

    doc, dups, suffixed = build_library()
    entries = doc["entries"]
    by_top: dict = {}
    unparsed = [e for e in entries.values() if e["canon"]["status"] == "unparsed"]
    seeded = [e for e in entries.values() if e["composer"] or e["inject"] or e["trim"]]
    for e in entries.values():
        top = e["path"].split("/")[0] if "/" in e["path"] else "."
        by_top[top] = by_top.get(top, 0) + 1
    print(f"{len(entries)} MIDI files across {len(by_top)} top-level dirs")
    for top, n in sorted(by_top.items()):
        print(f"  {n:5d}  {top}")
    print(f"{len(seeded)} seeded from corpus.json, {len(unparsed)} unparsed, "
          f"{len(suffixed)} collision-suffixed ids, {len(dups)} duplicate-content groups")
    for e in unparsed[:10]:
        print(f"  unparsed: {e['path']} ({e['canon']['reason']})")

    problems = crosscheck(doc)
    for p in problems:
        print("ERROR:", p, file=sys.stderr)
    if problems:
        return 1
    print("id cross-check: all published import ids reproduced exactly")

    if args.write_local:
        with open(args.write_local, "w") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote {args.write_local}")
        return 0
    if args.dry_run:
        return 0
    bucket = admin_bucket()
    stage_and_upload(doc, bucket, args.force)
    verify(doc, bucket)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
