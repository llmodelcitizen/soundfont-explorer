#!/usr/bin/env python3
"""Program-change injection for MIDI files that have none (LilyPond exports).

LilyPond writes no Program Change unless ``midiInstrument`` is set, so every
channel would come out as Acoustic Grand Piano.  This module applies a list of
rules from ``corpus.json`` (``"inject": [...]``) to an ``smf.Smf`` in place:

    {"track": 1, "channel": 1, "program": 56, "name": "Trumpet"}
    {"track": 2, "channel": 2, "program": 60, "name": "French Horn",
     "split": [{"below_note": 50, "channel": 3, "program": 58, "name": "Tuba"},
               {"from_note": 84, "channel": 4, "program": 72, "name": "Piccolo"}]}

* ``track`` is the 0-based SMF track index, ``channel`` the 1-based MIDI channel
  the rule applies to (10 = percussion).
* ``program`` (0-127) is inserted as a Program Change at tick 0 on that channel,
  placed before any other channel event of the track.  An identical program
  change already at tick 0 is left alone; differing ones at tick 0 are replaced
  (later in-song program changes are never touched).
* ``split`` optionally moves notes (note-on/off, poly aftertouch) whose key is
  ``< below_note`` and/or ``>= from_note`` to another channel with its own
  program; every non-note channel message (CC, bend, channel pressure) on the
  source channel is duplicated to the split channel so volume/pan/bend still
  apply.
* After all rules ran, ``check_programs`` verifies that *every* channel that has
  notes gets a program change before its first note (drum channel 10 included:
  GM synths ignore it, but banks that map drum kits to programs need it).

Usage as a script (debugging):  inject_programs.py in.mid out.mid rules.json
"""
from __future__ import annotations

import json
import sys
from typing import Dict, List, Tuple

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smf as S  # noqa: E402

NOTE_TYPES = (0x80, 0x90, 0xA0)


def _program_event(channel0: int, program: int) -> S.Event:
    return S.Event(0, "channel", 0xC0 | channel0, bytes([program]))


def _insert_program_at_zero(track: List[S.Event], channel0: int, program: int) -> str:
    """Ensure a program change (channel0, program) at tick 0; returns what was done."""
    existing = [e for e in track if e.kind == "channel" and e.tick == 0
                and e.type == 0xC0 and e.channel == channel0]
    if existing and all(e.data[0] == program for e in existing):
        return "kept"
    for e in existing:
        track.remove(e)
    # first channel event position (keep tick-0 meta events in front)
    idx = next((i for i, e in enumerate(track) if e.kind == "channel"), len(track))
    track.insert(idx, _program_event(channel0, program))
    return "replaced" if existing else "added"


def apply_rule(smf: S.Smf, rule: dict) -> List[str]:
    log = []
    ti = int(rule["track"])
    ch0 = int(rule["channel"]) - 1
    if not 0 <= ch0 <= 15:
        raise ValueError("channel must be 1..16")
    track = smf.tracks[ti]
    for sp in rule.get("split", []):
        dst0 = int(sp["channel"]) - 1
        below = sp.get("below_note")
        frm = sp.get("from_note")
        if below is None and frm is None:
            raise ValueError("split needs below_note and/or from_note")
        moved = 0
        dup: List[S.Event] = []
        for ev in track:
            if ev.kind != "channel" or ev.channel != ch0:
                continue
            if ev.type in NOTE_TYPES:
                key = ev.data[0]
                if (below is not None and key < below) or (frm is not None and key >= frm):
                    ev.status = (ev.status & 0xF0) | dst0
                    moved += 1
            elif ev.type in (0xB0, 0xD0, 0xE0):
                dup.append(S.Event(ev.tick, "channel", (ev.status & 0xF0) | dst0, ev.data))
        # duplicate controllers right after their originals (stable sort keeps order)
        track.extend(dup)
        track.sort(key=lambda e: e.tick)
        what = _insert_program_at_zero(track, dst0, int(sp["program"]))
        log.append("track %d: %d notes ch%d->ch%d, program %d (%s) %s" % (
            ti, moved, ch0 + 1, dst0 + 1, int(sp["program"]), sp.get("name", "?"), what))
    what = _insert_program_at_zero(track, ch0, int(rule["program"]))
    log.append("track %d ch%d: program %d (%s) %s" % (ti, ch0 + 1, int(rule["program"]), rule.get("name", "?"), what))
    return log


def apply_rules(smf: S.Smf, rules: List[dict]) -> List[str]:
    log = []
    for r in rules:
        log.extend(apply_rule(smf, r))
    return log


def channels_with_notes(smf: S.Smf) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for ev in smf.all_events():
        if ev.kind == "channel" and ev.is_note_on():
            out[ev.channel] = out.get(ev.channel, 0) + 1
    return out


def programs_at_zero(smf: S.Smf) -> Dict[int, int]:
    """channel0 -> program of the *first* tick-0 program change on that channel."""
    out: Dict[int, int] = {}
    for track in smf.tracks:
        for ev in track:
            if ev.kind == "channel" and ev.type == 0xC0 and ev.tick == 0 and ev.channel not in out:
                out[ev.channel] = ev.data[0]
    return out


DRUM_CHANNEL = 10   # 1-based; GM percussion

def check_programs(smf: S.Smf) -> List[int]:
    """Return 1-based channels whose first note-on is not preceded by a program change.

    "Preceded" = an earlier tick anywhere in the file, or the same tick but
    earlier in the same track (that is how injected tick-0 changes are placed).

    Channel 10 counts too: the program there selects the drum *kit*, and leaving it implicit lets
    different engines and banks pick different kits, which defeats the point of an A/B. Imports
    satisfy it with default_drum_program() rather than by exempting the channel.
    """
    first_note: Dict[int, Tuple[int, int, int]] = {}   # ch -> (tick, track, index)
    first_prog: Dict[int, Tuple[int, int, int]] = {}
    for ti, track in enumerate(smf.tracks):
        for i, ev in enumerate(sorted(track, key=lambda e: e.tick)):
            if ev.kind != "channel":
                continue
            if ev.is_note_on():
                first_note.setdefault(ev.channel, (ev.tick, ti, i))
            elif ev.type == 0xC0:
                cur = first_prog.get(ev.channel)
                if cur is None or (ev.tick, ti, i) < cur:
                    first_prog[ev.channel] = (ev.tick, ti, i)
    bad = []
    for ch, n in first_note.items():
        p = first_prog.get(ch)
        if p is None or p[0] > n[0] or (p[0] == n[0] and p[1] == n[1] and p[2] > n[2]) \
                or (p[0] == n[0] and p[1] != n[1] and p[1] > n[1]):
            bad.append(ch + 1)
    return sorted(bad)


def default_drum_rules(smf: S.Smf) -> List[dict]:
    """Rules that make an import's implied drum kit explicit.

    Only channel 10, and only program 0. GM already defines the standard kit as the default there,
    so writing it changes nothing about how the file is meant to sound — it just stops each engine
    from choosing for itself. Melodic channels are deliberately NOT defaulted: program 0 would be
    piano, and a file that really has no program change should fail loudly instead of quietly
    rendering as piano on all 566 variants.
    """
    missing = set(check_programs(smf))
    if DRUM_CHANNEL not in missing:
        return []
    track = 0
    for ti, tr in enumerate(smf.tracks):
        if any(e.kind == "channel" and e.channel == DRUM_CHANNEL - 1 and e.is_note_on() for e in tr):
            track = ti
            break
    return [{"track": track, "channel": DRUM_CHANNEL, "program": 0, "name": "standard kit"}]


def main(argv: List[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    src, dst, rules_path = argv[1:]
    with open(rules_path) as fh:
        rules = json.load(fh)
    m = S.load(src)
    for line in apply_rules(m, rules):
        print(line)
    missing = check_programs(m)
    if missing:
        print("WARNING: channels whose first note has no preceding program change:", missing)
    S.save(m, dst)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
