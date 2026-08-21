"""Minimal Standard MIDI File (SMF) parser and writer — Python stdlib only.

Supports format 0 and 1 (format 2 is rejected), running status, sysex
(F0 / F7) and meta events.  Events are kept in absolute ticks so tracks can be
edited freely and re-serialised; the writer recomputes deltas and never uses
running status (every engine accepts that, and it is byte-exact on re-read).

Data model
----------
    Smf(format, division, tracks)          tracks: list[list[Event]]
    Event(tick, kind, status, data, meta_type)
        kind   'channel' | 'meta' | 'sysex'
        status channel events: full status byte (0x80..0xEF)
               sysex: 0xF0 or 0xF7
               meta:  0xFF
        data   bytes (channel: 1 or 2 data bytes; meta/sysex: payload)
        meta_type  int for meta events, else None

Tempo handling lives in ``TempoMap`` (tick -> seconds, honouring every
set-tempo meta event on every track, which is what players actually do for
format 1 files even though the spec says tempo belongs on track 0).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

DEFAULT_TEMPO_US = 500000  # 120 bpm


@dataclass
class Event:
    tick: int
    kind: str                 # 'channel' | 'meta' | 'sysex'
    status: int
    data: bytes = b""
    meta_type: Optional[int] = None

    # --- convenience -------------------------------------------------
    @property
    def channel(self) -> Optional[int]:
        return (self.status & 0x0F) if self.kind == "channel" else None

    @property
    def type(self) -> Optional[int]:
        """High nibble of a channel message (0x80 note-off ... 0xE0 bend)."""
        return (self.status & 0xF0) if self.kind == "channel" else None

    def is_note_on(self) -> bool:
        return self.type == 0x90 and self.data[1] != 0

    def is_note_off(self) -> bool:
        return self.type == 0x80 or (self.type == 0x90 and self.data[1] == 0)

    def is_tempo(self) -> bool:
        return self.kind == "meta" and self.meta_type == 0x51

    def tempo_us(self) -> int:
        return int.from_bytes(self.data[:3], "big")


@dataclass
class Smf:
    format: int
    division: int
    tracks: List[List[Event]] = field(default_factory=list)

    def all_events(self) -> Iterable[Event]:
        for t in self.tracks:
            yield from t

    def last_tick(self) -> int:
        return max((ev.tick for ev in self.all_events()), default=0)


# ----------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------
class SmfError(ValueError):
    pass


def read_vlq(buf: bytes, pos: int):
    value = 0
    for _ in range(4):
        if pos >= len(buf):
            raise SmfError("truncated variable-length quantity")
        b = buf[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos
    raise SmfError("variable-length quantity longer than 4 bytes")


def write_vlq(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative VLQ")
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


_DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


def parse_track(buf: bytes) -> List[Event]:
    events: List[Event] = []
    pos = 0
    tick = 0
    running: Optional[int] = None
    n = len(buf)
    while pos < n:
        delta, pos = read_vlq(buf, pos)
        tick += delta
        if pos >= n:
            raise SmfError("truncated track")
        b = buf[pos]
        if b == 0xFF:
            mtype = buf[pos + 1]
            length, p = read_vlq(buf, pos + 2)
            data = bytes(buf[p:p + length])
            if len(data) != length:
                raise SmfError("truncated meta event")
            pos = p + length
            events.append(Event(tick, "meta", 0xFF, data, mtype))
            if mtype == 0x2F:
                break  # end of track — ignore any garbage after it
            continue
        if b in (0xF0, 0xF7):
            length, p = read_vlq(buf, pos + 1)
            data = bytes(buf[p:p + length])
            if len(data) != length:
                raise SmfError("truncated sysex event")
            pos = p + length
            events.append(Event(tick, "sysex", b, data))
            running = None
            continue
        if b & 0x80:
            status = b
            pos += 1
            if status >= 0xF0:
                raise SmfError("unexpected system message 0x%02X in track" % status)
            running = status
        else:
            if running is None:
                raise SmfError("data byte 0x%02X without running status" % b)
            status = running
        ln = _DATA_LEN[status & 0xF0]
        data = bytes(buf[pos:pos + ln])
        if len(data) != ln:
            raise SmfError("truncated channel event")
        pos += ln
        events.append(Event(tick, "channel", status, data))
    return events


def parse(blob: bytes) -> Smf:
    if blob[:4] != b"MThd":
        # some files have a RIFF RMID wrapper — look for the MThd inside
        i = blob.find(b"MThd")
        if i < 0:
            raise SmfError("not a Standard MIDI File (no MThd)")
        blob = blob[i:]
    hlen = struct.unpack(">I", blob[4:8])[0]
    fmt, ntrk, div = struct.unpack(">HHH", blob[8:14])
    if fmt not in (0, 1):
        raise SmfError("unsupported SMF format %d" % fmt)
    if div & 0x8000:
        raise SmfError("SMPTE time division is not supported")
    pos = 8 + hlen
    tracks = []
    while len(tracks) < ntrk and pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        clen = struct.unpack(">I", blob[pos + 4:pos + 8])[0]
        body = blob[pos + 8:pos + 8 + clen]
        pos += 8 + clen
        if cid != b"MTrk":
            continue  # skip alien chunks per the spec
        tracks.append(parse_track(body))
    if len(tracks) != ntrk:
        raise SmfError("header promises %d tracks, found %d" % (ntrk, len(tracks)))
    return Smf(fmt, div, tracks)


def load(path) -> Smf:
    with open(path, "rb") as fh:
        return parse(fh.read())


# ----------------------------------------------------------------------
# writing
# ----------------------------------------------------------------------
def serialize_track(events: List[Event]) -> bytes:
    out = bytearray()
    last = 0
    evs = sorted(events, key=lambda e: e.tick)  # stable: preserves order at equal ticks
    has_eot = False
    for ev in evs:
        if ev.kind == "meta" and ev.meta_type == 0x2F:
            continue  # re-added at the very end exactly once
        if ev.tick < last:
            raise SmfError("negative delta")
        out += write_vlq(ev.tick - last)
        last = ev.tick
        if ev.kind == "channel":
            out.append(ev.status)
            out += ev.data
        elif ev.kind == "sysex":
            out.append(ev.status)
            out += write_vlq(len(ev.data))
            out += ev.data
        else:
            out.append(0xFF)
            out.append(ev.meta_type)
            out += write_vlq(len(ev.data))
            out += ev.data
    end_tick = max(last, max((e.tick for e in evs), default=0))
    out += write_vlq(end_tick - last)
    out += b"\xff\x2f\x00"
    return bytes(out)


def serialize(smf: Smf) -> bytes:
    out = bytearray(b"MThd")
    out += struct.pack(">IHHH", 6, smf.format, len(smf.tracks), smf.division)
    for t in smf.tracks:
        body = serialize_track(t)
        out += b"MTrk" + struct.pack(">I", len(body)) + body
    return bytes(out)


def save(smf: Smf, path) -> None:
    with open(path, "wb") as fh:
        fh.write(serialize(smf))


# ----------------------------------------------------------------------
# timing
# ----------------------------------------------------------------------
class TempoMap:
    """tick -> seconds using every set-tempo event in the file."""

    def __init__(self, smf: Smf):
        self.division = smf.division
        changes = sorted(
            ((ev.tick, ev.tempo_us()) for ev in smf.all_events() if ev.is_tempo()),
            key=lambda x: x[0],
        )
        # collapse same-tick changes: last one wins
        collapsed = []
        for tick, tempo in changes:
            if collapsed and collapsed[-1][0] == tick:
                collapsed[-1] = (tick, tempo)
            else:
                collapsed.append((tick, tempo))
        if not collapsed or collapsed[0][0] > 0:
            collapsed.insert(0, (0, DEFAULT_TEMPO_US))
        # precompute cumulative seconds at each change
        self.points = []  # (tick, tempo_us, seconds_at_tick)
        secs = 0.0
        prev_tick, prev_tempo = collapsed[0][0], collapsed[0][1]
        self.points.append((prev_tick, prev_tempo, 0.0))
        for tick, tempo in collapsed[1:]:
            secs += (tick - prev_tick) * prev_tempo / 1e6 / self.division
            self.points.append((tick, tempo, secs))
            prev_tick, prev_tempo = tick, tempo

    def seconds(self, tick: int) -> float:
        # binary search would be nicer; tempo maps are short
        p = self.points[0]
        for q in self.points:
            if q[0] <= tick:
                p = q
            else:
                break
        return p[2] + (tick - p[0]) * p[1] / 1e6 / self.division

    def tick_at(self, seconds: float) -> int:
        """Inverse mapping (rounded to the nearest tick)."""
        p = self.points[0]
        for q in self.points:
            if q[2] <= seconds:
                p = q
            else:
                break
        return p[0] + int(round((seconds - p[2]) * 1e6 * self.division / p[1]))


def note_end_tick(smf: Smf) -> int:
    """Tick of the last note-off (or the end of the last still-sounding note).

    Returns 0 if the file has no notes.  A note-on without a matching note-off
    counts as ending at the track's end-of-track tick (what players do).
    """
    last = 0
    for track in smf.tracks:
        active = {}
        eot = max((e.tick for e in track), default=0)
        for ev in sorted(track, key=lambda e: e.tick):
            if ev.kind != "channel":
                continue
            if ev.is_note_on():
                active[(ev.channel, ev.data[0])] = ev.tick
            elif ev.is_note_off():
                active.pop((ev.channel, ev.data[0]), None)
                last = max(last, ev.tick)
        if active:
            last = max(last, eot)
    return last


def midi_end_seconds(smf: Smf) -> float:
    return TempoMap(smf).seconds(note_end_tick(smf))
