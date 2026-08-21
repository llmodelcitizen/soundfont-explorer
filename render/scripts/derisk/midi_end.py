#!/usr/bin/env python3
"""Tiny stdlib SMF parser: report the end time of a MIDI file in seconds.

Prints JSON with
  last_note_s   time of the last note-off (or note-on vel 0) -> "midi_end" for D
  last_event_s  time of the last event of any kind (incl. End-of-Track)
  D_s           ceil((last_note_s + 3) / 2) * 2   (plan §4)

Handles format 0/1 (tempo map from all tracks, PPQN division) and SMPTE division.
"""
import json
import math
import struct
import sys


def _vlq(data, i):
    v = 0
    while True:
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def parse(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"MThd":
        raise ValueError("not a standard MIDI file")
    hlen, fmt, ntrk, div = struct.unpack(">IHHH", data[4:14])
    pos = 8 + hlen
    tracks = []
    for _ in range(ntrk):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError("bad track chunk at %d" % pos)
        tlen = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        tracks.append(data[pos + 8:pos + 8 + tlen])
        pos += 8 + tlen

    tempo_changes = []  # (tick, us_per_qn)
    last_note_tick = 0
    last_event_tick = 0
    for trk in tracks:
        i = 0
        tick = 0
        status = 0
        while i < len(trk):
            delta, i = _vlq(trk, i)
            tick += delta
            b = trk[i]
            if b == 0xFF:
                mtype = trk[i + 1]
                ln, j = _vlq(trk, i + 2)
                payload = trk[j:j + ln]
                i = j + ln
                if mtype == 0x51 and ln == 3:
                    tempo_changes.append((tick, int.from_bytes(payload, "big")))
                last_event_tick = max(last_event_tick, tick)
                if mtype == 0x2F:
                    break
                continue
            if b in (0xF0, 0xF7):
                ln, j = _vlq(trk, i + 1)
                i = j + ln
                last_event_tick = max(last_event_tick, tick)
                continue
            if b & 0x80:
                status = b
                i += 1
            hi = status & 0xF0
            if hi in (0xC0, 0xD0):
                i += 1
            else:
                d1, d2 = trk[i], trk[i + 1]
                i += 2
                if hi == 0x80 or (hi == 0x90 and d2 == 0):
                    last_note_tick = max(last_note_tick, tick)
                elif hi == 0x90:
                    last_note_tick = max(last_note_tick, tick)  # note-on without off still sounds
            last_event_tick = max(last_event_tick, tick)

    def tick_to_s(t):
        if div & 0x8000:  # SMPTE
            fps = -(struct.unpack(">b", bytes([div >> 8]))[0])
            tpf = div & 0xFF
            return t / (fps * tpf)
        ppqn = div
        tempo_changes.sort()
        s = 0.0
        cur_tick = 0
        cur_tempo = 500000
        for ct, tp in tempo_changes:
            if ct >= t:
                break
            s += (ct - cur_tick) * cur_tempo / 1e6 / ppqn
            cur_tick = ct
            cur_tempo = tp
        s += (t - cur_tick) * cur_tempo / 1e6 / ppqn
        return s

    last_note_s = tick_to_s(last_note_tick)
    last_event_s = tick_to_s(last_event_tick)
    return {
        "format": fmt, "tracks": ntrk, "division": div,
        "tempo_changes": len(tempo_changes),
        "last_note_s": round(last_note_s, 6),
        "last_event_s": round(last_event_s, 6),
        "D_s": int(math.ceil((last_note_s + 3) / 2) * 2),
    }


if __name__ == "__main__":
    print(json.dumps(parse(sys.argv[1]), indent=2))
