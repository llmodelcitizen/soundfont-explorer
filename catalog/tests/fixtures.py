"""In-code synthetic SoundFont builder for the catalog tests (no binary fixtures)."""

from __future__ import annotations

import struct

EOP = ("EOP", 0, 0)


def chunk(fourcc: str, payload: bytes) -> bytes:
    """A RIFF chunk: fourcc, u32 size, payload, pad byte when the size is odd."""
    assert len(fourcc) == 4
    return fourcc.encode("ascii") + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) & 1 else b"")


def list_chunk(list_type: str, body: bytes) -> bytes:
    return chunk("LIST", list_type.encode("ascii") + body)


def zstr(text: str, pad_to_odd: bool = False) -> bytes:
    """A NUL-terminated INFO string; optionally forced to an odd byte length."""
    raw = text.encode("latin-1") + b"\0"
    if pad_to_odd and len(raw) % 2 == 0:
        raw += b"\0"
    return raw


def phdr_record(name: str, preset: int, bank: int, bag: int = 0) -> bytes:
    return struct.pack("<20sHHHIII", name.encode("latin-1")[:20].ljust(20, b"\0"), preset, bank, bag, 0, 0, 0)


def build_sf2(
    presets=((("Piano", 0, 0), ("Strings", 48, 0), ("Standard Kit", 0, 128))),
    info: dict | None = None,
    ifil=(2, 1),
    sdta_bytes: int = 64,
    pdta_prefix: bytes = b"",
    with_eop: bool = True,
    riff_size_override: int | None = None,
) -> bytes:
    """Return the bytes of a minimal but structurally valid sfbk RIFF."""
    info = {
        "INAM": "Test Font",
        "ICRD": "Aug 16, 1998",
        "IENG": "Tester",
        "IPRD": "SBAWE32",
        "ICOP": "Copyright 1998 Tester",
        "ICMT": "A comment",
        "ISFT": "handmade",
        "isng": "EMU8000",
        **(info or {}),
    }
    body = chunk("ifil", struct.pack("<HH", *ifil))
    for key, value in info.items():
        if value is None:
            continue
        body += chunk(key, value if isinstance(value, bytes) else zstr(value))
    info_list = list_chunk("INFO", body)
    sdta_list = list_chunk("sdta", chunk("smpl", bytes(range(256)) * (sdta_bytes // 256) + bytes(sdta_bytes % 256)))
    phdr = b"".join(phdr_record(*p) for p in presets)
    if with_eop:
        phdr += phdr_record(*EOP, bag=len(presets))
    pdta_body = pdta_prefix + chunk("phdr", phdr)
    # the other mandatory pdta sub-chunks, minimal terminal records only
    pdta_body += chunk("pbag", b"\0" * 4) + chunk("pmod", b"\0" * 10) + chunk("pgen", b"\0" * 4)
    pdta_body += chunk("inst", b"\0" * 22) + chunk("ibag", b"\0" * 4) + chunk("imod", b"\0" * 10) + chunk("igen", b"\0" * 4)
    pdta_body += chunk("shdr", b"\0" * 46)
    pdta_list = list_chunk("pdta", pdta_body)
    payload = b"sfbk" + info_list + sdta_list + pdta_list
    size = len(payload) if riff_size_override is None else riff_size_override
    return b"RIFF" + struct.pack("<I", size) + payload


def sdta_range(data: bytes) -> tuple[int, int]:
    """(start, end) byte offsets of the LIST/sdta payload inside a build_sf2() blob."""
    pos = 12
    while pos + 12 <= len(data):
        fourcc = data[pos : pos + 4]
        (size,) = struct.unpack_from("<I", data, pos + 4)
        if fourcc == b"LIST" and data[pos + 8 : pos + 12] == b"sdta":
            return pos + 12, pos + 8 + size
        pos += 8 + size + (size & 1)
    raise AssertionError("no sdta in fixture")
