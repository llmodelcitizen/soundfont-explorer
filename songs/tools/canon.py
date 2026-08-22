#!/usr/bin/env python3
"""Canonicalize the song corpus: songs/corpus.json -> songs/<id>.mid + songs/songs.json.

For every entry in corpus.json (and every owner-supplied file in
songs/private/, see below):

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
  6. write songs/<id>.mid (deterministic bytes: sorted events, no running
     status) and record sha256, ``midi_end_s`` and
     ``duration_s = D = ceil((midi_end + tail) / slice_s) * slice_s`` in songs.json (slice_s from render/engines.json).

Private songs: every ``*.mid`` / ``*.MID`` directly inside songs/private/ is
processed the same way with ``license.id = "owner-supplied"``, id
``private-<slug>``, output songs/private/canon/<id>.mid and an entry in
songs/private/songs.json.  Optional per-file metadata (title, composer, trim,
inject ...) can be given in songs/private/corpus.json keyed by file name.

    python3 songs/tools/canon.py            # regenerate everything
    python3 songs/tools/canon.py --check    # re-run and fail if songs.json would change
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
    """(slice_s, tail_s) as declared in render/engines.json ``render`` (corpus.json may override tail_s)."""
    with open(ENGINES_JSON, encoding="utf-8") as fh:
        r = json.load(fh)["render"]
    return r["slice_s"], r["tail_s"]


SLICE_S, DEFAULT_TAIL_S = _render_constants()
assert float(SLICE_S).is_integer() and SLICE_S > 0, f"render.slice_s must be a whole number of seconds, got {SLICE_S!r}"


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
    'Honky-Tonk Piano'), which is why an explicit `title` in corpus.json always wins.
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


def import_labels(src: str, smf: S.Smf, explicit_title: Optional[str] = None) -> Tuple[str, str, str]:
    """(id, title, output path) for a song imported from songs/import/FILES/.

    The label keeps the file's directory path and ends with the song's own name:
    ``videogame-music/crystalis/Crystalis Desert`` when the MIDI carries a title,
    ``videogame-music/crystalis/crys_cave`` when it does not. The canonical MIDI keeps the
    original *filename* under songs/rendered/, so the file on disk still matches its source.
    """
    rel = src[len(IMPORT_ROOT):] if src.startswith(IMPORT_ROOT) else src
    rel = re.sub(r"\.midi?$", "", rel, flags=re.I)
    parent, _, stem = rel.rpartition("/")
    name = explicit_title or sequence_title(smf) or stem
    title = f"{parent}/{name}" if parent else name
    return slug(title), title, os.path.join(RENDERED_DIR, parent, stem + ".mid")


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
    if spec.get("_imported"):
        # imports almost never write the drum channel's program. Make the GM default explicit so
        # every engine picks the same kit; melodic channels still have to be right in the source.
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
def license_block(corpus: dict, spec: dict) -> dict:
    lic = dict(corpus["licenses"][spec["license"]])
    out = {"id": lic["id"], "url": lic["url"]}
    if "license_notice" in spec:
        out["notice_text"] = spec["license_notice"]
    elif "notice_file" in lic:
        with open(os.path.join(SONGS_DIR, lic["notice_file"]), encoding="utf-8") as fh:
            out["notice_text"] = fh.read().strip()
    else:
        out["notice_text"] = lic["notice_text"].format(**{**spec, "title": spec["title"]})
    return out


def load_source(spec: dict) -> Tuple[S.Smf, Optional[bytes]]:
    gen = spec.get("generator")
    if gen == "make_diagnostic":
        import make_diagnostic
        m = make_diagnostic.build()
        return m, S.serialize(m)
    path = os.path.join(SONGS_DIR, spec["src"])
    with open(path, "rb") as fh:
        blob = fh.read()
    return S.parse(blob), blob


def _signature(smf: S.Smf) -> List[int]:
    """Per-track count of events other than end-of-track (the writer re-adds those)."""
    return [sum(1 for e in t if not (e.kind == "meta" and e.meta_type == 0x2F)) for t in smf.tracks]


def build_entry(corpus: dict, spec: dict, out_dir: str, private: bool = False) -> Tuple[dict, bytes]:
    tail_s = float(corpus.get("tail_s", DEFAULT_TAIL_S))
    smf, src_blob = load_source(spec)
    smf, info = canonicalize(smf, spec, tail_s)
    blob = S.serialize(smf)
    # round-trip sanity: the canonical bytes must parse back to the same event count
    back = S.parse(blob)
    assert _signature(back) == _signature(smf), "serializer mismatch"
    out_name = spec["id"] + ".mid"
    out_path = os.path.join(out_dir, out_name)
    if spec.get("_out_path"):          # imported: keep the source's directory tree and filename
        out_path = spec["_out_path"]
        out_name = os.path.relpath(out_path, SONGS_DIR)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(blob)
    classes = list(spec.get("include_classes") or corpus.get("default_include_classes", []))
    classes += [c for c in spec.get("extra_include_classes", []) if c not in classes]
    if private:
        lic = {"id": "owner-supplied", "url": None,
               "notice_text": "Owner-supplied file from songs/private/ (not redistributed with the repository)."}
        file_rel = "private/canon/" + out_name
    else:
        lic = license_block(corpus, spec)
        # out_name is already relative to songs/ for imports; plain songs live at rendered/<id>.mid
        file_rel = out_name if out_name.startswith("rendered/") else "rendered/" + out_name
    entry = {
        "id": spec["id"],
        "title": spec["title"],
        "composer": spec.get("composer", "unknown"),
        "sequencer": spec.get("sequencer", "unknown"),
        "source_url": spec.get("source_url"),
        "license": lic,
        "modifications": info["modifications"],
        "midi_end_s": info["midi_end_s"],
        "duration_s": info["duration_s"],
        "sha256": sha256_bytes(blob),
        "include_classes": classes,
        "default": spec["id"] == corpus.get("default"),
        "file": file_rel,
        "src": spec.get("src"),
        "src_sha256": sha256_bytes(src_blob) if src_blob is not None else None,
        "original_midi_end_s": info["original_midi_end_s"],
        "original_last_event_s": info["original_last_event_s"],
        "end_marker_s": info["end_marker_s"],
        "format": smf.format,
        "division": smf.division,
        "tracks": len(smf.tracks),
    }
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


def stable_header(corpus: dict) -> dict:
    return {
        "schema": SCHEMA,
        "generated_by": "songs/tools/canon.py",
        "tail_s": corpus.get("tail_s", DEFAULT_TAIL_S),
        "canonicalization": ("events re-serialised without running status; text meta 'soundfont-explorer:end' "
                             f"appended on track 0 at midi_end + tail_s; duration_s = ceil((midi_end + tail_s)/{SLICE_S:g})*{SLICE_S:g}"),
    }


def resolve_import(spec: dict) -> dict:
    """Fill in id/title/output path for a spec whose src is under songs/import/FILES/."""
    src = spec.get("src") or ""
    if not src.startswith(IMPORT_ROOT):
        return spec
    smf, _ = load_source(spec)
    sid, title, out_path = import_labels(src, smf, spec.get("title"))
    return {**spec, "id": spec.get("id") or sid, "title": title, "_out_path": out_path,
            "_imported": True}


def run_public(corpus: dict, check: bool) -> Tuple[List[dict], bool]:
    entries = []
    for spec in corpus["songs"]:
        spec = resolve_import(spec)
        entry, _ = build_entry(corpus, spec, RENDERED_DIR)
        entries.append(entry)
        print("  %-22s midi_end=%8.3f s  D=%4d s  sha=%s  %s" % (
            entry["id"], entry["midi_end_s"], entry["duration_s"], entry["sha256"][:12],
            "" if entry["modifications"] == "none" else "(modified)"))
    ids = [e["id"] for e in entries]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate song ids")
    if corpus.get("default") not in ids:
        raise SystemExit("default song %r not in corpus" % corpus.get("default"))
    data = stable_header(corpus)
    data["default"] = corpus["default"]
    data["songs"] = entries
    ok = write_json(os.path.join(SONGS_DIR, "songs.json"), data, check)
    return entries, ok


def run_private(corpus: dict, check: bool) -> Tuple[List[dict], bool]:
    pdir = os.path.join(SONGS_DIR, "private")
    if not os.path.isdir(pdir):
        return [], True
    files = sorted(f for f in os.listdir(pdir)
                   if f.lower().endswith(".mid") and os.path.isfile(os.path.join(pdir, f)))
    if not files:
        return [], True
    overrides = {}
    opath = os.path.join(pdir, "corpus.json")
    if os.path.exists(opath):
        with open(opath, encoding="utf-8") as fh:
            overrides = json.load(fh)
    out_dir = os.path.join(pdir, "canon")
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    for f in files:
        stem = os.path.splitext(f)[0]
        spec = {"id": "private-" + slug(stem), "src": "private/" + f, "title": stem,
                "composer": "unknown", "sequencer": "unknown", "source_url": None}
        spec.update(overrides.get(f, {}))
        entry, _ = build_entry(corpus, spec, out_dir, private=True)
        entries.append(entry)
        print("  %-22s midi_end=%8.3f s  D=%4d s  sha=%s  (private)" % (
            entry["id"], entry["midi_end_s"], entry["duration_s"], entry["sha256"][:12]))
    data = stable_header(corpus)
    data["songs"] = entries
    ok = write_json(os.path.join(pdir, "songs.json"), data, check)
    return entries, ok


def main(argv: List[str]) -> int:
    check = "--check" in argv
    with open(os.path.join(SONGS_DIR, "corpus.json"), encoding="utf-8") as fh:
        corpus = json.load(fh)
    print("public corpus:")
    _, ok1 = run_public(corpus, check)
    print("private songs:")
    _, ok2 = run_private(corpus, check)
    if check and not (ok1 and ok2):
        print("songs.json is out of date — run canon.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
