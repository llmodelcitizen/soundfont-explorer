#!/usr/bin/env python3
"""One-time migration: the last two corpus paths -> the admin library.

Until 2026-08-25 a MIDI could become a song three ways: a hand-written spec in
songs/corpus.json, a file dropped in songs/private/, or a library entry. The library did
everything the other two did — and pins an id at upload so a rename never orphans a render —
so the other two are gone. This script moves what they held into the library:

  * the 13 curated tracks (7 Freedoom, 2 Joplin, Bach, Sousa, Casual Afternoon and the
    generated GM diagnostic) with their **pinned ids**. freedoom-e1m1 must stay
    freedoom-e1m1: it is the site's default track and ~14,150 rendered variants plus every
    share link are keyed by these ids. The entries are seeded with the id, never minted from
    the title the way an upload does.
  * their licences: `license` names a key in songs/licenses.json and `license_fields` fills
    that licence's notice template. Freedoom's BSD-3 attribution reaching the published
    songs.json is a redistribution obligation, not a nicety.
  * songs/private/STARWARS.MID, which renormalises on purpose: it becomes an ordinary import
    and takes the id an ordinary import would give it. Its private-starwars renders are
    orphaned by that, deliberately — re-render and prune afterwards.

Idempotent and re-runnable: a track that already has an entry is left completely alone (a move
since is reported, never undone), a destination path some other entry owns is refused rather
than seeded over, and no entry is ever overwritten or deleted. Entries written before
library.json carried the publishing fields are backfilled with the defaults, so the document
comes out uniform.

First fetch the pre-migration songs.json, so there is a baseline to check the result against
(the repository no longer ships one — it is generated from the library):

    aws s3 cp s3://<admin-bucket>/canon/songs.json songs/songs.json

then:

    admin/scripts/seed_library.py --dry-run           # plan only; reads library.json, writes nothing
    admin/scripts/seed_library.py                     # seed against the admin bucket
    admin/scripts/seed_library.py --library FILE      # seed a local library.json (testing)
    admin/scripts/seed_library.py --verify            # after a Canon check: diff vs the baseline

Afterwards, on the admin box (the local mirror is what canon.py reads through
songs/import/FILES):

    aws s3 sync s3://<admin-bucket>/library/FILES/ /opt/sfadmin/data/library/FILES/ --size-only
    systemctl restart sfadmin        # re-reads library.json from S3

then run a full Canon check, `--verify` here, and re-render/prune the STARWARS id.

Needs: aws CLI authenticated; infra/live/outputs.json with the `admin` output; the sources
still in songs/src/ and songs/private/ (both gitignored, both on the workstation only).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "songs", "tools"))
sys.path.insert(0, os.path.join(ROOT, "admin", "server"))
import canon  # noqa: E402
import make_diagnostic  # noqa: E402
import smf as S  # noqa: E402
from sfadmin.clock import now_iso  # noqa: E402
from sfadmin.entries import backfill, canon_state, new_entry  # noqa: E402

SONGS = os.path.join(ROOT, "songs")
BASELINE = os.path.join(ROOT, "work", "seed-library-baseline.json")

PIANO = [{"track": 1, "channel": 1, "program": 0, "name": "Acoustic Grand Piano"},
         {"track": 2, "channel": 2, "program": 0, "name": "Acoustic Grand Piano"}]
FREEDOOM_URL = ("https://github.com/freedoom/freedoom/blob/"
                "d14dbbee3b6fbfb2c11cdb65eb61216e86d4ee85/musics/")


def _freedoom(sid: str, stem: str, name: str, who: str) -> dict:
    return {"id": sid, "src": f"src/freedoom-{stem}.mid",
            "dest": f"videogame-music/freedoom/freedoom-{stem}.mid", "name": name,
            "composer": who, "sequencer": who, "source_url": FREEDOOM_URL + stem + ".mid",
            "license": "freedoom-bsd3"}


#: The curated corpus, verbatim from the songs/corpus.json this replaces. `id` is pinned;
#: `dest` is the file's path inside the library, and its directory is the folder the track
#: shows up in on the site (canon derives songs.json's `path` from it).
CURATED = [
    _freedoom("freedoom-e1m1", "d_e1m1", "Steel and Brass (Freedoom E1M1)", "J.Astartes and Mr795"),
    _freedoom("freedoom-map20", "d_map20", "Wrath of Horizon (Freedoom MAP20)", "J.Astartes"),
    _freedoom("freedoom-e2m5", "d_e2m5", "Reminiscence (Freedoom E2M5)", "Korp"),
    _freedoom("freedoom-e2m4", "d_e2m4", "Diodine (Freedoom E2M4)", "Korp"),
    _freedoom("freedoom-e3m8", "d_e3m8", "The Zenith (Freedoom E3M8)", 'Lola "BlueWorrior" Harvey'),
    _freedoom("freedoom-map02", "d_map02", "Dark Underworld (Freedoom MAP02)", 'Lola "BlueWorrior" Harvey'),
    _freedoom("freedoom-map01", "d_map01", "Not My First Rodeo... (Freedoom MAP01)", 'Lola "BlueWorrior" Harvey'),
    {
        "id": "joplin-maple-leaf", "src": "src/mutopia-maple.mid",
        "dest": "EricsFavorites/mutopia-maple.mid", "name": "Maple Leaf Rag",
        "composer": "Scott Joplin", "sequencer": "Chris Sawer",
        "source_url": "https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=23",
        "license": "mutopia-pd", "license_fields": {"mutopia_id": "Mutopia-2011/11/13-23"},
        "extra_include_classes": ["single_instrument:piano"], "inject": PIANO,
    },
    {
        "id": "joplin-entertainer", "src": "src/mutopia-entertainer.mid",
        "dest": "miscellaneous/mutopia-entertainer.mid", "name": "The Entertainer",
        "composer": "Scott Joplin", "sequencer": "Chris Sawer",
        "source_url": "https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=263",
        "license": "mutopia-pd", "license_fields": {"mutopia_id": "Mutopia-2016/11/25-263"},
        "extra_include_classes": ["single_instrument:piano"], "inject": PIANO,
    },
    {
        "id": "bach-bwv565", "src": "src/mutopia-BWV565-ToccataFugue.mid",
        "dest": "mutopia-BWV565-ToccataFugue.mid",
        "name": "Toccata in D minor, BWV 565 (Toccata section)",
        "composer": "Johann Sebastian Bach", "sequencer": "Anonymous (Mutopia)",
        "source_url": "https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=1780",
        "license": "mutopia-pd", "license_fields": {"mutopia_id": "Mutopia-2011/09/11-1780"},
        "trim": {
            "cut_tick": 45312, "hold_to_tick": 46080,
            "why": ("The source is the complete Toccata AND Fugue (9:32 at the file's fixed 60 bpm). "
                    "The Toccata section ends with the D-minor chord on beats 1-2 of bar 30 (tick "
                    "44544-45312) under a fermata; the fugue subject enters on beat 3 of bar 30. "
                    "There is no rest or cadence anywhere near 2:45 (bar 42 is mid-exposition with a "
                    "running sixteenth-note voice), so the cut is placed at the fugue's entry (tick "
                    "45312 = 118.0 s) and the written fermata is realised by holding the final chord "
                    "to the bar line (tick 46080 = 120.0 s)."),
        },
        "inject": [{"track": t, "channel": t, "program": 19, "name": "Church Organ"} for t in (1, 2, 3)],
    },
    {
        "id": "sousa-stars-stripes", "src": "src/mutopia-TheStarsAndStripesForever.mid",
        "dest": "mutopia-TheStarsAndStripesForever.mid", "name": "The Stars and Stripes Forever",
        "composer": "John Philip Sousa", "sequencer": "Benjamin Bloomfield",
        "source_url": "https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=626",
        "license": "mutopia-pd", "license_fields": {"mutopia_id": "Mutopia-2017/11/06-626"},
        "inject_note": ("Mutopia's file is the 1897 John Church piano edition: two tracks ('upper' "
                        "ch1 54-92, 'lower' ch2 32-68) with no program changes. It is re-voiced as a "
                        "small wind band by pitch: upper -> Trumpet, with everything from C6 (84) up "
                        "-> Piccolo (the trio's obbligato and the octave doublings); lower -> French "
                        "Horn for the after-beat chords, with everything below D3 (50) -> Tuba for "
                        "the bass line."),
        "inject": [
            {"track": 1, "channel": 1, "program": 56, "name": "Trumpet",
             "split": [{"from_note": 84, "channel": 4, "program": 72, "name": "Piccolo"}]},
            {"track": 2, "channel": 2, "program": 60, "name": "French Horn",
             "split": [{"below_note": 50, "channel": 3, "program": 58, "name": "Tuba"}]},
        ],
    },
    {
        "id": "oga-casual-afternoon", "src": "src/oga-roppychop-casual-afternoon.mid",
        "dest": "oga-roppychop-casual-afternoon.mid", "name": "Casual Afternoon",
        "composer": "roppychop (Roppy Chop Studios)", "sequencer": "roppychop (Roppy Chop Studios)",
        "source_url": "https://opengameart.org/content/original-midi-album",
        "license": "cc0", "license_fields": {
            "cc0_source": ('OpenGameArt.org "Original MIDI Album", '
                           "https://opengameart.org/content/original-midi-album")},
    },
    {
        # generated, not downloaded: canon.py used to build it from a `generator` spec, which
        # was the last reason for a song to exist outside the library. The bytes are the same
        # either way (make_diagnostic is deterministic), so the canonical sha256 does not move.
        "id": "sfp-diagnostic", "generator": True, "dest": "sfp-diagnostic.mid",
        "name": "Soundfont Explorer GM diagnostic", "composer": "Soundfont Explorer (generated)",
        "sequencer": "songs/tools/make_diagnostic.py",
        "source_url": "https://github.com/quaintops/soundfont-explorer/blob/main/songs/tools/make_diagnostic.py",
        "license": "cc0", "extra_include_classes": ["drums_only"],
        "license_fields": {"cc0_source": "generated for this project, see LICENSES/sfp-diagnostic-cc0.txt"},
    },
]

#: songs/private/, which had exactly one file. The owner chose to let this one renormalise —
#: its private-starwars renders are orphaned on purpose — but chose the id `starwars`, so it is
#: pinned to that. Left unpinned it would mint `ericsfavorites-starwars`: import_labels slugs
#: <dir>/<stem>, so the folder name would end up welded to the id, which is neither what was
#: asked for nor something anyone would want to read in a share link.
PRIVATE = [{"id": "starwars", "src": "private/STARWARS.MID", "dest": "EricsFavorites/STARWARS.MID",
            "name": "Star Wars (main title)", "composer": "John Williams"}]

ENTRY_FIELDS = ("composer", "sequencer", "source_url", "license", "license_fields",
                "extra_include_classes", "inject", "trim", "inject_note")


# ---------------------------------------------------------------------- sources

def source_bytes(item: dict) -> bytes:
    if item.get("generator"):
        return S.serialize(make_diagnostic.build())
    path = os.path.join(SONGS, item["src"])
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        raise SystemExit(
            f"missing source {path} — songs/src/ and songs/private/ are gitignored and live only "
            "on the workstation; run this there, before deleting them") from None


def derive_id(item: dict, blob: bytes, taken: set) -> str:
    """The pinned id, or — for a track that renormalises — the one an upload would mint."""
    if item.get("id"):
        return item["id"]
    try:
        sid, _, _ = canon.import_labels("import/FILES/" + item["dest"], S.parse(blob), None)
    except Exception:
        sid, _ = canon.fallback_labels(item["dest"])
    base, n = sid, 2
    while sid in taken:
        sid = f"{base}-{n}"
        n += 1
    return sid


# ---------------------------------------------------------------------- library document

def admin_bucket() -> str:
    out = os.path.join(ROOT, "infra", "live", "outputs.json")
    try:
        return json.load(open(out))["admin"]["value"]["bucket"]
    except (OSError, KeyError, TypeError):
        raise SystemExit(f"no admin outputs in {out} — apply with -var enable_admin=true "
                         "and refresh it") from None


class Store:
    """Where library.json and the file bodies live: the admin bucket, or a local path."""

    def __init__(self, local: str | None, mirror: str | None) -> None:
        self.local = local
        self.mirror = mirror                       # extra local copy of the file bodies
        self.bucket = None if local else admin_bucket()

    def read_doc(self) -> dict:
        if self.local:
            try:
                with open(self.local) as fh:
                    return json.load(fh)
            except FileNotFoundError:
                return {"schema": 1, "updated_at": None, "entries": {}}
        r = subprocess.run(["aws", "s3", "cp", f"s3://{self.bucket}/library/library.json", "-"],
                           capture_output=True)
        if r.returncode != 0:
            raise SystemExit(f"cannot read library.json: {r.stderr.decode()[-500:]}")
        return json.loads(r.stdout)

    def write_doc(self, doc: dict) -> None:
        body = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        if self.local:
            with open(self.local, "w") as fh:
                fh.write(body)
            return
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(body)
            tmp = fh.name
        try:
            subprocess.run(["aws", "s3", "cp", tmp, f"s3://{self.bucket}/library/library.json",
                            "--content-type", "application/json", "--only-show-errors"], check=True)
        finally:
            os.unlink(tmp)

    def put_file(self, dest: str, blob: bytes) -> None:
        if not self.local:
            with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as fh:
                fh.write(blob)
                tmp = fh.name
            try:
                subprocess.run(["aws", "s3", "cp", tmp, f"s3://{self.bucket}/library/FILES/{dest}",
                                "--content-type", "audio/midi", "--only-show-errors"], check=True)
            finally:
                os.unlink(tmp)
        if self.mirror:
            dst = os.path.join(self.mirror, dest)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(blob)


# ---------------------------------------------------------------------- planning

def plan(doc: dict) -> tuple[list[dict], list[str], list[str]]:
    """(actions, skipped, problems). One action per track that is not in the library yet.

    "Already migrated" is asked of the pinned id where there is one, and otherwise of the
    destination path — an unpinned track has no id to recognise itself by, and asking the
    minted id instead would mint a *second* one on every re-run.
    """
    entries = doc["entries"]
    by_path = {e["path"]: e for e in entries.values()}
    actions, skipped, problems = [], [], []
    taken = set(entries)
    for item in CURATED + PRIVATE:
        existing = entries.get(item["id"]) if item.get("id") else by_path.get(item["dest"])
        if existing is not None:
            # already migrated: leave it exactly as it is. A different path means the owner
            # moved the track since — noted, never undone.
            where = ("" if existing["path"] == item["dest"]
                     else f" at {existing['path']} (this script would have put it at {item['dest']})")
            skipped.append(f"{existing['id']}: already present{where}")
            continue
        blob = source_bytes(item)
        sid = derive_id(item, blob, taken)
        clash = by_path.get(item["dest"])
        if clash is not None:
            problems.append(f"{item['dest']} is already entry {clash['id']!r} — refusing to "
                            f"seed {sid!r} over it")
            continue
        taken.add(sid)
        by_path[item["dest"]] = {"id": sid}
        actions.append({**item, "id": sid, "blob": blob,
                        "sha256": hashlib.sha256(blob).hexdigest()})
    return actions, skipped, problems


def seed(store: Store, doc: dict, actions: list[dict]) -> None:
    ts = now_iso()
    for a in actions:
        store.put_file(a["dest"], a["blob"])
        meta = {k: a[k] for k in ENTRY_FIELDS if a.get(k)}
        doc["entries"][a["id"]] = new_entry(
            a["id"], a["dest"], a["name"], sha256=a["sha256"], size=len(a["blob"]),
            added_at=ts, canon=canon_state("pending"), **meta)
    doc["updated_at"] = ts


# ---------------------------------------------------------------------- baseline / verify

#: What must not move. `path`, `file` and `src` all change by design — the canonical MIDI now
#: mirrors the source's place in the library instead of being named after the id.
INVARIANT = ("title", "composer", "sequencer", "source_url", "license", "modifications",
             "midi_end_s", "duration_s", "sha256", "include_classes", "default",
             "src_sha256", "original_midi_end_s", "end_marker_s", "format", "division", "tracks")


def songs_json() -> dict:
    with open(os.path.join(SONGS, "songs.json")) as fh:
        return {e["id"]: e for e in json.load(fh)["songs"]}


def write_baseline() -> None:
    """Snapshot today's songs.json so --verify can prove nothing moved. Written once: a second
    run must compare against the pre-migration truth, not against its own output."""
    if os.path.exists(BASELINE):
        print(f"baseline already at {BASELINE} (kept)")
        return
    try:
        songs = songs_json()
    except FileNotFoundError:
        print("WARNING: no songs/songs.json here, so no baseline and nothing for --verify to\n"
              "  compare against. Fetch the pre-migration one first and re-run:\n"
              "    aws s3 cp s3://<admin-bucket>/canon/songs.json songs/songs.json")
        return
    os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
    with open(BASELINE, "w") as fh:
        json.dump({k: {f: v.get(f) for f in INVARIANT} for k, v in songs.items()}, fh,
                  indent=1, sort_keys=True)
        fh.write("\n")
    print(f"baseline: {len(songs)} songs -> {BASELINE}")


def verify() -> int:
    """Compare the rebuilt songs.json against the baseline, field by field, for every id the
    baseline knows except the ones the migration deliberately renormalises."""
    with open(BASELINE) as fh:
        base = json.load(fh)
    now = songs_json()
    renamed = [sid for sid in base if sid.startswith("private-")]
    bad = 0
    for sid in sorted(base):
        if sid in renamed:
            print(f"  {sid}: renormalised on purpose — not compared "
                  f"({'still present!' if sid in now else 'gone, as expected'})")
            continue
        cur = now.get(sid)
        if cur is None:
            print(f"  MISSING {sid} — the migration lost a track")
            bad += 1
            continue
        diff = [f for f in INVARIANT if base[sid][f] != cur.get(f)]
        if diff:
            print(f"  CHANGED {sid}: {', '.join(diff)}")
            for f in diff:
                print(f"      was {base[sid][f]!r}\n      now {cur.get(f)!r}")
            bad += 1
    extra = sorted(set(now) - set(base))
    print(f"{len(base) - len(renamed) - bad} of {len(base) - len(renamed)} pre-existing songs "
          f"identical on every invariant field; {len(extra)} new "
          f"({', '.join(extra[:5])}{'…' if len(extra) > 5 else ''})")
    return 1 if bad else 0


# ---------------------------------------------------------------------- main

def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="plan only: reads library.json, writes nothing")
    ap.add_argument("--library", metavar="FILE",
                    help="seed this local library.json instead of the admin bucket")
    ap.add_argument("--library-dir", metavar="DIR",
                    help="also write the file bodies here (a box's library mirror)")
    ap.add_argument("--verify", action="store_true",
                    help="compare songs/songs.json against the baseline and stop")
    args = ap.parse_args(argv)
    if args.verify:
        return verify()

    store = Store(args.library, args.library_dir)
    doc = store.read_doc()
    before = doc.get("updated_at")
    actions, skipped, problems = plan(doc)

    for s in skipped:
        print("  skip:", s)
    for p in problems:
        print("  ERROR:", p, file=sys.stderr)
    for a in actions:
        print("  seed: %-38s <- %-42s -> library/FILES/%s  [%s]"
              % (a["id"], a.get("src", "songs/tools/make_diagnostic.py"), a["dest"],
                 a.get("license", "owner-supplied")))
    if problems:
        return 1
    filled = sum(1 for e in doc["entries"].values() if backfill(e))
    print(f"{len(actions)} to seed, {len(skipped)} already present, "
          f"{filled} existing entries backfilled with the new fields")
    write_baseline()
    if args.dry_run:
        print("dry run — nothing written")
        return 0
    if not actions and not filled:
        print("nothing to do")
        return 0
    # The only other writer is the admin server, which holds library.json's ETag and would
    # clobber this on its next mutation. Checked before anything is uploaded, so a conflict
    # costs nothing rather than leaving file bodies in the bucket with no entries naming them.
    if not args.library and store.read_doc().get("updated_at") != before:
        raise SystemExit("library.json changed while this ran (the admin server?) — nothing "
                         "written; stop sfadmin and re-run")
    seed(store, doc, actions)
    store.write_doc(doc)
    print(f"wrote library.json ({len(doc['entries'])} entries)")
    if not args.library:
        print("\nnext, on the admin box:\n"
              f"  aws s3 sync s3://{store.bucket}/library/FILES/ /opt/sfadmin/data/library/FILES/ --size-only\n"
              "  systemctl restart sfadmin\n"
              "then a full Canon check, then `admin/scripts/seed_library.py --verify` here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
