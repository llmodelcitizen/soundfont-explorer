"""Minimal Ogg/Opus container reader — exact sample counts without decoding.

samples = granule_position(last page) − pre_skip(OpusHead). Files here are ≤ 300 KB,
so a forward walk over all pages is fine and also validates page structure.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class OggError(ValueError):
    pass


@dataclass
class OpusInfo:
    pre_skip: int
    channels: int
    input_rate: int
    samples: int          # at 48 kHz, after pre-skip
    pages: int
    bytes: int

    @property
    def duration_s(self) -> float:
        return self.samples / 48000.0


def iter_pages(data: bytes):
    pos = 0
    n = len(data)
    while pos < n:
        if data[pos:pos + 4] != b"OggS":
            raise OggError(f"bad capture pattern at {pos}")
        if pos + 27 > n:
            raise OggError("truncated page header")
        version, htype = data[pos + 4], data[pos + 5]
        if version != 0:
            raise OggError("unsupported ogg version")
        granule = struct.unpack_from("<q", data, pos + 6)[0]
        nsegs = data[pos + 26]
        seg_table = data[pos + 27:pos + 27 + nsegs]
        if len(seg_table) != nsegs:
            raise OggError("truncated segment table")
        body_len = sum(seg_table)
        body = data[pos + 27 + nsegs: pos + 27 + nsegs + body_len]
        if len(body) != body_len:
            raise OggError("truncated page body")
        yield htype, granule, body, seg_table
        pos += 27 + nsegs + body_len


def opus_info(path: Path) -> OpusInfo:
    data = Path(path).read_bytes()
    pre_skip = channels = rate = None
    last_granule = -1
    pages = 0
    eos = False
    for i, (htype, granule, body, _segs) in enumerate(iter_pages(data)):
        pages += 1
        if i == 0:
            if not (htype & 0x02) or body[:8] != b"OpusHead":
                raise OggError("first page is not OpusHead")
            channels = body[9]
            pre_skip = struct.unpack_from("<H", body, 10)[0]
            rate = struct.unpack_from("<I", body, 12)[0]
        if granule >= 0:
            last_granule = granule
        if htype & 0x04:
            eos = True
    if pre_skip is None:
        raise OggError("no pages")
    if not eos:
        raise OggError("no EOS page (truncated file)")
    return OpusInfo(pre_skip=pre_skip, channels=channels or 0, input_rate=rate or 0,
                    samples=max(0, last_granule - pre_skip), pages=pages, bytes=len(data))
