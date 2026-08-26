#!/usr/bin/env python3
"""Canonicalize the song corpus: songs/corpus-imports.json -> songs/rendered/ + songs/songs.json.

There is exactly one way a MIDI file becomes a song: it is a file in the admin library.
library.json (the admin's document, S3) -> tools/fragment.py -> songs/corpus-imports.json
-> here.  The two older paths — hand-written specs in songs/corpus.json and owner-supplied
drops in songs/private/ — are gone (2026-08-25); the library did everything they did, with
pinned ids that survive a rename, and three paths meant three sets of rules for one job.

For every spec in the fragment:

  1. parse the source SMF (format 0 or 1);
  2. apply the optional trim (``trim.cut_tick``: drop everything from that tick
     on, close notes still sounding; ``trim.hold_to_tick``: move the note-offs
     that land exactly on the cut — i.e. the final chord — to that tick);
  3. apply optional program-change injection (``inject`` rules, see
     inject_programs.py) and verify every channel that has notes gets a program
     change before its first note;
  4. compute ``midi_end`` = time of the last note-off in seconds (every tempo
     change honoured);
  5. append a text meta event ``soundfont-explorer:end`` at ``midi_end + tail_s`` on
     track 0, so every engine keeps rendering through the release tail;
  6. write songs/rendered/<the source's path under import/FILES/> (deterministic
     bytes: sorted events, no running status) and record sha256, ``midi_end_s``
     and ``duration_s = D = ceil((midi_end + tail) / slice_s) * slice_s`` in
     songs.json (slice_s from render/engines.json).

Licences come from songs/licenses.json, keyed by the spec's ``license``; that file stays in
git because the notices it produces are a redistribution obligation, not box state.

    python3 songs/tools/canon.py            # regenerate everything
    python3 songs/tools/canon.py --check    # re-run and fail if songs.json would change

A checkout without songs/corpus-imports.json (which is generated, hence gitignored) has no
corpus: the sources live in the library's file store, not in the repository.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SONGS_DIR = os.path.dirname(HERE)
# canonical MIDIs live here; imported ones keep their source directory tree beneath it
RENDERED_DIR = os.path.join(SONGS_DIR, "rendered")
sys.path.insert(0, HERE)
import smf as S                      # noqa: E402
import inject_programs as IP         # noqa: E402

END_MARKER = b"soundfont-explorer:end"
SCHEMA = 1
ENGINES_JSON = os.path.join(os.path.dirname(SONGS_DIR), "render", "engines.json")


def _render_constants() -> Tuple[float, float]:
    """(slice_s, tail_s) as declared in render/engines.json ``render``."""
    with open(ENGINES_JSON, encoding="utf-8") as fh:
        r = json.load(fh)["render"]
    return r["slice_s"], r["tail_s"]


SLICE_S, TAIL_S = _render_constants()
assert float(SLICE_S).is_integer() and SLICE_S > 0, f"render.slice_s must be a whole number of seconds, got {SLICE_S!r}"

# The last two corpus-wide knobs corpus.json used to hold. They belong in git, not in the
# library document: freedoom-e1m1 is the track every share link without an explicit ?song=
# resolves to and ~14,150 rendered variants are keyed by, so it must not be editable state
# on the box; and the include-class set is render policy shared with render/cloud/planner.py.
DEFAULT_SONG_ID = "freedoom-e1m1"
DEFAULT_INCLUDE_CLASSES = ["full_gm", "melodic_only", "partial"]
#: What a library entry gets when nobody says otherwise (fragment.py emits the same default).
DEFAULT_LICENSE = "owner-supplied"


# ----------------------------------------------------------------------
# transformations
# ----------------------------------------------------------------------
def trim(smf: S.Smf, cut_tick: int, hold_to_tick: Optional[int] = None) -> dict:
    """Cut the file at ``cut_tick`` (exclusive).

    Events strictly before the cut are kept.  Note-offs exactly at the cut are
    kept; every other event at/after the cut is dropped.  Notes still sounding
    at the cut get a note-off at the cut.  With ``hold_to_tick`` the note-offs
    that fall exactly on the cut are moved there (a fermata on the final chord).
    """
    if hold_to_tick is not None and hold_to_tick < cut_tick:
        raise ValueError("hold_to_tick must be >= cut_tick")
    dropped = 0
    closed = 0
    held = 0
    for ti, track in enumerate(smf.tracks):
        kept: List[S.Event] = []
        active: Dict[Tuple[int, int], bool] = {}
        for ev in sorted(track, key=lambda e: e.tick):
            if ev.tick < cut_tick:
                kept.append(ev)
                if ev.kind == "channel":
                    if ev.is_note_on():
                        active[(ev.channel, ev.data[0])] = True
                    elif ev.is_note_off():
                        active.pop((ev.channel, ev.data[0]), None)
                continue
            if ev.tick == cut_tick and ev.kind == "channel" and ev.is_note_off() \
                    and (ev.channel, ev.data[0]) in active:
                kept.append(ev)
                active.pop((ev.channel, ev.data[0]), None)
                continue
            dropped += 1
        for (ch, key) in sorted(active):
            kept.append(S.Event(cut_tick, "channel", 0x80 | ch, bytes([key, 0])))
            closed += 1
        if hold_to_tick is not None:
            for ev in kept:
                if ev.tick == cut_tick and ev.kind == "channel" and ev.is_note_off():
                    ev.tick = hold_to_tick
                    held += 1
        smf.tracks[ti] = kept
    return {"dropped_events": dropped, "closed_notes": closed, "held_notes": held}


def append_end_marker(smf: S.Smf, midi_end_s: float, tail_s: float) -> Tuple[int, float]:
    """Append the ``soundfont-explorer:end`` text meta at >= midi_end + tail on track 0."""
    tm = S.TempoMap(smf)
    target = midi_end_s + tail_s
    tick = tm.tick_at(target)
    while tm.seconds(tick) < target - 1e-9:
        tick += 1
    # never place it before existing events on track 0
    tick = max(tick, max((e.tick for e in smf.tracks[0]), default=0))
    smf.tracks[0].append(S.Event(tick, "meta", 0xFF, END_MARKER, 0x01))
    return tick, tm.seconds(tick)


def duration_D(midi_end_s: float, tail_s: float, slice_s: float = SLICE_S) -> int:
    """Rendered length: midi_end + tail rounded up to a whole number of slices."""
    return int(math.ceil((midi_end_s + tail_s) / slice_s - 1e-9) * slice_s)


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


IMPORT_ROOT = "import/FILES/"


def sequence_title(smf: S.Smf) -> Optional[str]:
    """The file's own title, or None.

    SMF convention: in format 1 the Sequence/Track Name (meta 0x03) on **track 0** names the
    sequence, while the same meta on later tracks names an instrument or staff. So only track 0
    counts — otherwise a file whose first named track is 'Cave Music 1' or 'Lead' would be
    labelled with an instrument name. Real exports still get this wrong (a track 0 called
    'Honky-Tonk Piano'), which is why the library entry's own name always wins.
    """
    if not smf.tracks:
        return None
    for e in smf.tracks[0]:
        if e.kind == "meta" and e.meta_type == 0x03:
            t = e.data.decode("latin-1", "replace").strip()
            # Cakewalk and friends leak the source filename into the sequence name
            for ext in (".cw", ".mid", ".midi", ".wrk"):
                if t.lower().endswith(ext):
                    t = t[: -len(ext)]
            t = t.strip()
            return t or None
    return None


def import_out_name(rel: str) -> str:
    """songs/rendered/-relative name of an import's canonical MIDI, from its import/FILES/-relative
    source path: the path itself, plus ``.mid`` unless it already ends in exactly that.

    Stripping ``.mid``/``.midi`` case-insensitively and re-adding ``.mid`` made ``x.mid``,
    ``x.MID`` and ``x.midi`` (Windows-era archives have all three) land on one
    ``rendered/x.mid``: the last one canonicalized won and the other entries' ``sha256`` in
    songs.json no longer matched the file on disk. Keeping the source's own extension in the
    name maps distinct sources to distinct files (``x.MID.mid``, ``x.midi.mid``) while every
    ``.mid`` source — nearly all of them — keeps the name it always had. The admin's stale-file
    cleanup (sfadmin.library) uses this same function.
    """
    return rel if rel.endswith(".mid") else rel + ".mid"


def strip_midi_ext(rel: str) -> str:
    """The stem rule: ``.mid``/``.midi`` come off (any case), everything else — ``.rmi``
    included — stays. Titles lose the extension; the file on disk keeps it (import_out_name)."""
    return re.sub(r"\.midi?$", "", rel, flags=re.I)


def fallback_labels(rel: str, explicit_title: Optional[str] = None) -> Tuple[str, str]:
    """(id, title) for an import file whose SMF could not be parsed, from its
    import/FILES/-relative path alone: directory path plus the file's stem — exactly what
    import_labels() produces for a file that carries no sequence title. The admin server and
    admin/scripts/ingest.py both mint ids for unparsable uploads this way, and an id minted
    here is pinned in library.json forever, so the rule lives in one place."""
    parent, _, stem = strip_midi_ext(rel).rpartition("/")
    name = explicit_title or stem
    title = f"{parent}/{name}" if parent else name
    return slug(title), title


def import_labels(src: str, smf: S.Smf, explicit_title: Optional[str] = None) -> Tuple[str, str, str]:
    """(id, title, output path) for a song imported from songs/import/FILES/.

    The label keeps the file's directory path and ends with the song's own name:
    ``videogame-music/crystalis/Crystalis Desert`` when the MIDI carries a title,
    ``videogame-music/crystalis/crys_cave`` when it does not. The canonical MIDI keeps the
    original *filename* under songs/rendered/ (import_out_name), so the file on disk still
    matches its source.
    """
    rel = src[len(IMPORT_ROOT):] if src.startswith(IMPORT_ROOT) else src
    parent, _, stem = strip_midi_ext(rel).rpartition("/")
    name = explicit_title or sequence_title(smf) or stem
    title = f"{parent}/{name}" if parent else name
    return slug(title), title, os.path.join(RENDERED_DIR, import_out_name(rel))


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "song"


def canonicalize(smf: S.Smf, spec: dict, tail_s: float) -> Tuple[S.Smf, dict]:
    """Apply trim/inject/end-marker to ``smf`` in place; return (smf, info)."""
    info: dict = {"modifications": []}
    info["original_midi_end_s"] = round(S.midi_end_seconds(smf), 3)
    info["original_last_event_s"] = round(S.TempoMap(smf).seconds(smf.last_tick()), 3)
    t = spec.get("trim")
    if t:
        r = trim(smf, int(t["cut_tick"]), t.get("hold_to_tick"))
        why = t.get("why", "")
        tm = S.TempoMap(smf)
        desc = "trimmed at tick %d (%.1f s of %.1f s)" % (
            int(t["cut_tick"]), tm.seconds(int(t["cut_tick"])), info["original_midi_end_s"])
        if t.get("hold_to_tick") is not None:
            desc += ", final chord held to tick %d (%.1f s)" % (int(t["hold_to_tick"]), tm.seconds(int(t["hold_to_tick"])))
        if why:
            desc += ": " + why
        info["modifications"].append(desc)
        info["trim_result"] = r
    rules = spec.get("inject")
    if rules:
        log = IP.apply_rules(smf, rules)
        info["inject_log"] = log
        changed = [l for l in log if not l.endswith(" kept")]
        if changed:
            parts = []
            for rule in rules:
                p = "track %d ch%d -> %d %s" % (rule["track"], rule["channel"], rule["program"], rule.get("name", ""))
                for sp in rule.get("split", []):
                    cond = []
                    if sp.get("below_note") is not None:
                        cond.append("notes < %d" % sp["below_note"])
                    if sp.get("from_note") is not None:
                        cond.append("notes >= %d" % sp["from_note"])
                    p += " (%s -> ch%d %d %s)" % (" and ".join(cond), sp["channel"], sp["program"], sp.get("name", ""))
                parts.append(p)
            desc = "program changes injected at tick 0: " + "; ".join(parts)
            if spec.get("inject_note"):
                desc += ". " + spec["inject_note"]
            info["modifications"].append(desc)
        else:
            info["modifications"].append(
                "program changes already present in the source (" + ", ".join(
                    "ch%d=%d" % (rule["channel"], rule["program"]) for rule in rules) + "), none injected")
    # Real-world MIDI almost never writes the drum channel's program. Make the GM default
    # explicit so every engine picks the same kit; melodic channels still have to be right in
    # the source. (This used to be conditional on the song being an import — now every song
    # is one. It is a no-op for a file that names its ch10 program, which is why turning it on
    # for the once-curated tracks did not move a single canonical sha256.)
    drum = IP.default_drum_rules(smf)
    if drum:
        IP.apply_rules(smf, drum)
        info["modifications"].append(
            "program change injected at tick 0: ch10 = 0 (GM standard kit, implicit in the source)")
    missing = IP.check_programs(smf)
    if missing:
        raise SystemExit("%s: channels whose first note has no preceding program change: %s"
                         % (spec.get("id"), missing))
    midi_end = S.midi_end_seconds(smf)
    tick, secs = append_end_marker(smf, midi_end, tail_s)
    info["midi_end_s"] = round(midi_end, 3)
    info["end_marker_tick"] = tick
    info["end_marker_s"] = round(secs, 3)
    info["duration_s"] = duration_D(midi_end, tail_s)
    if not info["modifications"]:
        info["modifications"] = "none"
    else:
        info["modifications"] = " | ".join(info["modifications"])
    return smf, info


# ----------------------------------------------------------------------
# corpus driver
# ----------------------------------------------------------------------
def load_licenses() -> dict:
    """The licence table from songs/licenses.json, keyed the way a spec's ``license`` names it."""
    with open(os.path.join(SONGS_DIR, "licenses.json"), encoding="utf-8") as fh:
        return json.load(fh)["licenses"]


def license_block(licenses: dict, spec: dict) -> dict:
    """The published ``license`` object: id, url and the notice text to reproduce.

    A missing key is a hard refusal rather than a silent fallback to owner-supplied: the
    Freedoom tracks' BSD-3 attribution has to reach songs.json or the site is redistributing
    them without their notice.
    """
    key = spec.get("license") or DEFAULT_LICENSE
    if key not in licenses:
        raise SystemExit("%s: unknown license %r (songs/licenses.json defines %s)"
                         % (spec.get("id"), key, ", ".join(sorted(licenses))))
    lic = licenses[key]
    out = {"id": lic["id"], "url": lic["url"]}
    if "notice_file" in lic:
        with open(os.path.join(SONGS_DIR, lic["notice_file"]), encoding="utf-8") as fh:
            out["notice_text"] = fh.read().strip()
    else:
        # the spec's own keys, then the entry's license_fields ({mutopia_id}, {cc0_source}):
        # placeholders the licence template needs that are not song metadata
        fields = {**spec, **(spec.get("license_fields") or {}), "title": spec["title"]}
        try:
            out["notice_text"] = lic["notice_text"].format(**fields)
        except KeyError as e:
            raise SystemExit("%s: license %r has no license_fields[%s] to fill its notice"
                             % (spec.get("id"), key, e)) from None
    return out


def load_source(spec: dict) -> Tuple[S.Smf, bytes]:
    path = os.path.join(SONGS_DIR, spec["src"])
    with open(path, "rb") as fh:
        blob = fh.read()
    return S.parse(blob), blob


def _signature(smf: S.Smf) -> List[int]:
    """Per-track count of events other than end-of-track (the writer re-adds those)."""
    return [sum(1 for e in t if not (e.kind == "meta" and e.meta_type == 0x2F)) for t in smf.tracks]


def build_entry(spec: dict, licenses: dict, default_id: str = DEFAULT_SONG_ID) -> Tuple[dict, bytes]:
    smf, src_blob = load_source(spec)
    smf, info = canonicalize(smf, spec, TAIL_S)
    blob = S.serialize(smf)
    # round-trip sanity: the canonical bytes must parse back to the same event count
    back = S.parse(blob)
    assert _signature(back) == _signature(smf), "serializer mismatch"
    # the canonical MIDI mirrors the source's path under import/FILES/ (resolve_import)
    out_path = spec["_out_path"]
    out_name = os.path.relpath(out_path, SONGS_DIR)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(blob)
    classes = list(DEFAULT_INCLUDE_CLASSES)
    classes += [c for c in spec.get("extra_include_classes") or [] if c not in classes]
    entry = {
        "id": spec["id"],
        "title": spec["title"],
        "composer": spec.get("composer") or "unknown",
        "sequencer": spec.get("sequencer") or "unknown",
        "source_url": spec.get("source_url"),
        "license": license_block(licenses, spec),
        "modifications": info["modifications"],
        "midi_end_s": info["midi_end_s"],
        "duration_s": info["duration_s"],
        "sha256": sha256_bytes(blob),
        "include_classes": classes,
        "default": spec["id"] == default_id,
        "file": out_name,
        "src": spec.get("src"),
        "src_sha256": sha256_bytes(src_blob),
        "original_midi_end_s": info["original_midi_end_s"],
        "original_last_event_s": info["original_last_event_s"],
        "end_marker_s": info["end_marker_s"],
        "format": smf.format,
        "division": smf.division,
        "tracks": len(smf.tracks),
    }
    if "path" in spec:
        entry["path"] = spec["path"] or None
    if spec.get("inject"):
        entry["programs"] = spec["inject"]
    if spec.get("trim"):
        entry["trim"] = spec["trim"]
    return entry, blob


def write_json(path: str, data: dict, check: bool) -> bool:
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    old = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
    if check:
        return old == text
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return True


def stable_header() -> dict:
    return {
        "schema": SCHEMA,
        "generated_by": "songs/tools/canon.py",
        "tail_s": TAIL_S,
        "canonicalization": ("events re-serialised without running status; text meta 'soundfont-explorer:end' "
                             f"appended on track 0 at midi_end + tail_s; duration_s = ceil((midi_end + tail_s)/{SLICE_S:g})*{SLICE_S:g}"),
    }


def resolve_import(spec: dict) -> dict:
    """Fill in title/output path for one spec; every song's source is under songs/import/FILES/.

    The id is whatever the fragment pinned (library.json pins it at upload and never changes
    it again, so a rename or a move never orphans a render). import_labels() is still the
    fallback for a spec that carries none — the same rule that minted the pinned ids.
    """
    src = spec.get("src") or ""
    if not src.startswith(IMPORT_ROOT):
        raise SystemExit("%s: src %r is not under %s — every song is a library file"
                         % (spec.get("id"), src, IMPORT_ROOT))
    smf, _ = load_source(spec)
    sid, title, out_path = import_labels(src, smf, spec.get("title"))
    # the directory lives in "path" and the title stays the leaf name — the published entry
    # carries both, and the public client builds its folder tree from them
    title = spec.get("title") or title.rpartition("/")[2]
    return {**spec, "id": spec.get("id") or sid, "title": title, "_out_path": out_path}


def load_specs() -> List[dict]:
    """Every song spec, from songs/corpus-imports.json (fragment.py's view of the library).

    No fragment is not an empty corpus, it is a tree that has never been told what the corpus
    IS — the sources live in the library's file store and the repository ships the tools, not
    the music. Refuse: a workstation that has synced songs.json down for a render run would
    otherwise have it blanked by a stray canon.py. A fragment listing no songs is different —
    that is a library with nothing visible in it, and writing it out is correct."""
    fpath = os.path.join(SONGS_DIR, "corpus-imports.json")
    if not os.path.exists(fpath):
        raise SystemExit("no songs/corpus-imports.json — build it first:\n"
                         "  python3 songs/tools/fragment.py --library <library.json>")
    with open(fpath, encoding="utf-8") as fh:
        return json.load(fh)["songs"]


def run_public(specs: List[dict], check: bool, lenient: bool = False,
               only: Optional[set] = None,
               default_id: str = DEFAULT_SONG_ID) -> Tuple[List[dict], bool, List[dict], List[str]]:
    """(entries, songs.json unchanged, per-song refusals, ids dropped from songs.json).
    `dropped` is only ever non-empty for a targeted (--only) run; it rides in
    canon-report.json so the admin UI can show it — the printed version below goes to
    stdout, which the admin inherits into the journal and never shows (#19)."""
    licenses = load_licenses()
    entries: List[dict] = []
    refused: List[dict] = []
    dropped: List[str] = []
    for spec in specs:
        sid = spec.get("id")
        if only is not None and sid is not None and sid not in only:
            continue
        try:
            spec = resolve_import(spec)
        except (Exception, SystemExit) as e:  # unparseable source; check_programs is later
            if not lenient:
                raise
            refused.append({"id": sid, "src": spec.get("src"), "reason": str(e)})
            continue
        if only is not None and spec["id"] not in only:
            continue
        try:
            entry, _ = build_entry(spec, licenses, default_id)
        except (Exception, SystemExit) as e:  # check_programs refusal, trim/inject errors, ...
            if not lenient:
                raise
            refused.append({"id": spec["id"], "src": spec.get("src"), "reason": str(e)})
            continue
        entries.append(entry)
        print("  %-22s midi_end=%8.3f s  D=%4d s  sha=%s  %s" % (
            entry["id"], entry["midi_end_s"], entry["duration_s"], entry["sha256"][:12],
            "" if entry["modifications"] == "none" else "(modified)"))
    if only is not None:
        # incremental: replace/append the selected songs in the existing songs.json so an
        # admin metadata edit does not re-canonicalize the whole library. songs.json is
        # generated (the repository ships no corpus), so a box that has never run canon has
        # none — then a targeted run is simply the first entry in a new one.
        try:
            with open(os.path.join(SONGS_DIR, "songs.json"), encoding="utf-8") as fh:
                existing = json.load(fh)["songs"]
        except FileNotFoundError:
            existing = []
        by_id = {e["id"]: e for e in entries}
        merged: List[dict] = []
        for e in existing:
            if e["id"] in by_id:
                merged.append(by_id.pop(e["id"]))       # re-canonicalized this run
            elif e["id"] not in only:
                merged.append(e)                        # not selected: untouched
            else:
                # selected but not produced (refused, or gone from the corpus): its stale
                # entry must not survive, or the render list goes on offering a song canon
                # has just rejected
                dropped.append(e["id"])
        entries = merged + list(by_id.values())
        if dropped:
            # the admin's publish path refuses to drop a track that is live on the site, so
            # this is also the moment that path stops working for it — say so here rather
            # than at the next "Republish songs.json" (#19)
            print("  dropped from songs.json (selected, not produced): %s" % ", ".join(dropped))
            print("  if one of those is live on the site, publishing is blocked until it is "
                  "fixed, hidden (Library) or removed (Published tab)")
    ids = [e["id"] for e in entries]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate song ids")
    if entries and default_id not in ids:
        # an empty corpus is a checkout with no fragment (nothing to default to); a non-empty
        # one that lost the default track is a real fault — the site would open on whatever
        # sorts first, and every share link without ?song= would land somewhere else
        msg = "default song %r not in corpus" % default_id
        why = next((r for r in refused if r.get("id") == default_id), None)
        if why:  # a lenient run swallowed the real error; surface it
            msg += " — it was refused: %s" % why["reason"]
        elif refused:
            msg += " — %d songs were refused (first: %s: %s)" % (
                len(refused), refused[0]["id"] or refused[0]["src"], refused[0]["reason"])
        raise SystemExit(msg)
    data = stable_header()
    data["default"] = default_id
    data["songs"] = entries
    ok = write_json(os.path.join(SONGS_DIR, "songs.json"), data, check)
    return entries, ok, refused, dropped


def main(argv: List[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail if songs.json would change")
    ap.add_argument("--lenient", action="store_true",
                    help="collect per-song failures into songs/canon-report.json instead of aborting "
                         "(imports are wild; a refusal is data, not an error)")
    ap.add_argument("--only", action="append", metavar="ID",
                    help="canonicalize only these song ids and merge them into the existing "
                         "songs.json (repeatable; entries without a pinned id cannot be selected)")
    args = ap.parse_args(argv)
    if args.only and args.check:
        ap.error("--only cannot be combined with --check")
    specs = load_specs()
    print("corpus: %d songs" % len(specs))
    _, ok, refused, dropped = run_public(specs, args.check, lenient=args.lenient,
                                         only=set(args.only) if args.only else None,
                                         default_id=DEFAULT_SONG_ID)
    if args.lenient:
        # `dropped` is how the admin UI learns what a targeted run cost: the printed
        # version above goes to canon.py's stdout, which library.canon_run inherits
        # into the sfadmin journal on purpose and never shows the operator (#19)
        report = {"schema": 1, "refused": refused, "dropped": dropped}
        with open(os.path.join(SONGS_DIR, "canon-report.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        if refused:
            print("%d refused (songs/canon-report.json):" % len(refused))
            for r in refused[:20]:
                print("  %s: %s" % (r["id"] or r["src"], r["reason"]))
    if args.check and not ok:
        print("songs.json is out of date — run canon.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
