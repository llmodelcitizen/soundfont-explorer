"""Tiny SMF type-0 writer (only what calibration needs)."""
from __future__ import annotations

import struct
from pathlib import Path


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def write_smf0(path: Path, events: list[tuple[int, bytes]], ppq: int = 480) -> None:
    """events: (absolute_tick, raw bytes) — must include a tempo meta if not 120 bpm."""
    events = sorted(events, key=lambda e: e[0])
    data = bytearray()
    t = 0
    for tick, raw in events:
        data += _vlq(tick - t) + raw
        t = tick
    data += _vlq(0) + b"\xff\x2f\x00"
    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq))
        f.write(b"MTrk" + struct.pack(">I", len(data)) + bytes(data))


def click_track(path: Path, clicks_s=(0.0, 170.0), tail_s: float = 3.0, program: int = 115, note: int = 76,
                dur_s: float = 0.05, ppq: int = 480, bpm: int = 120) -> None:
    """Woodblock clicks (GM program 115) at the given seconds; text marker at end+tail."""
    tps = ppq * bpm / 60.0
    ev: list[tuple[int, bytes]] = [
        (0, b"\xff\x51\x03" + (60_000_000 // bpm).to_bytes(3, "big")),   # tempo
        (0, b"\xf0\x05\x7e\x7f\x09\x01\xf7"),                              # GM System On
        (0, bytes([0xC0, program])),
        (0, bytes([0xB0, 7, 127])),
    ]
    last = 0.0
    for c in clicks_s:
        on = int(round(c * tps))
        off = int(round((c + dur_s) * tps))
        ev.append((on, bytes([0x90, note, 127])))
        ev.append((off, bytes([0x80, note, 0])))
        last = max(last, c + dur_s)
    end_tick = int(round((last + tail_s) * tps))
    ev.append((end_tick, b"\xff\x01\x03end"))
    write_smf0(path, ev, ppq)
