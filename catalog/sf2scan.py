"""Scan a directory of SoundFont 2 files and write ``catalog/soundfonts.json``.

Stdlib only (Python >= 3.12).  The scanner never reads sample data: it walks the
RIFF structure chunk by chunk, slurps ``LIST/INFO`` (capped at 1 MiB), seeks
through ``LIST/pdta`` to ``phdr`` and skips ``LIST/sdta`` entirely.  A separate
thread pool computes the full sha256 of every file (the only pass over the
sample data; ~2 min for 50 GB on a warm NVMe).

Usage::

    python3 -m catalog.sf2scan [--root soundfonts] [--out catalog/soundfonts.json]
                               [--limit N] [--threads 8] [--no-hash] [--quiet]

Output schema: see ``catalog/README.md`` ("soundfonts.json").
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from ._util import now_iso, write_json

SCHEMA = 1
INFO_CAP = 1 << 20  # 1 MiB: never slurp more of LIST/INFO than this
PHDR_RECORD = 38  # sfPresetHeader: name[20] preset u16 bank u16 bagNdx u16 lib u32 genre u32 morph u32
PHDR_STRUCT = struct.Struct("<20sHHHIII")
PHDR_CAP = PHDR_RECORD * 65536  # u16 bag indices bound the preset count; anything larger is corruption
HASH_CHUNK = 8 << 20
DEFAULT_THREADS = 8

# INFO sub-chunks exposed in the output (always present; null when absent).
INFO_KEYS = ("INAM", "ICRD", "IENG", "IPRD", "ICOP", "ICMT", "ISFT", "isng")


class SF2Error(Exception):
    """Raised for a structurally broken SoundFont (reported as parse_ok=false)."""


# --------------------------------------------------------------------------- RIFF


def _read_exact(fh, n: int, what: str) -> bytes:
    data = fh.read(n)
    if len(data) != n:
        raise SF2Error(f"truncated file while reading {what} (wanted {n}, got {len(data)})")
    return data


def _decode_text(raw: bytes) -> str:
    """Decode a ZSTR: stop at the first NUL, UTF-8 then latin-1 fallback, strip."""
    raw = raw.split(b"\0", 1)[0]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return text.replace("\0", "").strip()


def _iter_subchunks(buf: bytes):
    """Yield (fourcc, payload) for the sub-chunks inside an in-memory LIST body.

    Tolerates a truncated tail (the INFO cap) by yielding what is there.
    """
    pos = 0
    end = len(buf)
    while pos + 8 <= end:
        fourcc = buf[pos : pos + 4]
        (size,) = struct.unpack_from("<I", buf, pos + 4)
        payload = buf[pos + 8 : pos + 8 + size]
        yield fourcc.decode("latin-1"), payload
        pos += 8 + size + (size & 1)


def parse_info(buf: bytes) -> dict:
    """Parse a LIST/INFO body (fourcc 'INFO' already consumed)."""
    ifil = None
    iver = None
    fields: dict = {k: None for k in INFO_KEYS}
    for fourcc, payload in _iter_subchunks(buf):
        if fourcc == "ifil":
            if len(payload) >= 4:
                major, minor = struct.unpack_from("<HH", payload, 0)
                ifil = f"{major}.{minor}"
        elif fourcc == "iver":
            if len(payload) >= 4:
                major, minor = struct.unpack_from("<HH", payload, 0)
                iver = f"{major}.{minor}"
        elif fourcc in fields:
            fields[fourcc] = _decode_text(payload)
    return {"ifil": ifil, "iver": iver, "fields": fields}


def parse_phdr(buf: bytes) -> list[tuple[str, int, int]]:
    """Decode phdr records into (name, preset, bank) tuples, dropping the EOP terminator.

    The SF2 spec mandates a terminal record (conventionally named "EOP"); it is
    always the last record and is never a real preset, so it is dropped by
    position, not by name.
    """
    if len(buf) % PHDR_RECORD != 0:
        raise SF2Error(f"phdr size {len(buf)} is not a multiple of {PHDR_RECORD}")
    n = len(buf) // PHDR_RECORD
    if n < 1:
        raise SF2Error("phdr has no records (missing EOP terminator)")
    records = []
    for i in range(n - 1):  # drop the terminal EOP record
        name, preset, bank, _bag, _lib, _genre, _morph = PHDR_STRUCT.unpack_from(buf, i * PHDR_RECORD)
        records.append((_decode_text(name), preset, bank))
    return records


def _walk_riff(fh, file_size: int) -> dict:
    """Walk the top-level chunks of an sfbk RIFF, returning INFO + phdr raw data."""
    header = _read_exact(fh, 12, "RIFF header")
    if header[:4] != b"RIFF":
        raise SF2Error("not a RIFF file")
    (riff_size,) = struct.unpack_from("<I", header, 4)
    if header[8:12] != b"sfbk":
        raise SF2Error(f"RIFF form is {header[8:12]!r}, not 'sfbk'")
    warnings = []
    declared = 8 + riff_size
    if declared != file_size:
        warnings.append(f"RIFF size field says {declared} bytes, file is {file_size}")

    info = None
    phdr = None
    seen = []
    pos = 12
    end = file_size  # lenient: walk to the real EOF even if the RIFF size field lies
    while pos + 8 <= end:
        fh.seek(pos)
        chunk_hdr = fh.read(8)
        if len(chunk_hdr) < 8:
            break
        fourcc = chunk_hdr[:4]
        (size,) = struct.unpack_from("<I", chunk_hdr, 4)
        body_start = pos + 8
        if fourcc == b"LIST":
            if size < 4:
                # a LIST cannot even hold its type; `size - 4` below would go negative, and
                # fh.read(-1) slurps the whole file (fh.read(-2..-4) is a ValueError)
                raise SF2Error(f"LIST chunk at {pos} declares {size} bytes (needs >= 4 for its type)")
            list_type = _read_exact(fh, 4, "LIST type")
            seen.append(list_type.decode("latin-1"))
            if list_type == b"INFO":
                want = min(size - 4, INFO_CAP)
                buf = fh.read(want)
                if size - 4 > INFO_CAP:
                    warnings.append(f"LIST/INFO is {size - 4} bytes; parsed only the first {INFO_CAP}")
                info = parse_info(buf)
            elif list_type == b"pdta":
                # the parent's declared end is a u32 from the file: clamp it to the real EOF so a
                # lying size cannot license a multi-GiB read further down
                phdr = _find_phdr(fh, body_start + 4, min(body_start + size, file_size), warnings)
            # sdta (and anything unknown) is skipped without reading.
        else:
            seen.append(fourcc.decode("latin-1"))
        pos = body_start + size + (size & 1)

    if info is None:
        raise SF2Error(f"no LIST/INFO chunk (top-level chunks: {seen})")
    if phdr is None:
        raise SF2Error(f"no LIST/pdta/phdr chunk (top-level chunks: {seen})")
    return {"info": info, "phdr": phdr, "warnings": warnings, "chunks": seen}


def _find_phdr(fh, start: int, end: int, warnings: list) -> bytes:
    """Seek through a LIST/pdta body (sub-chunks) and return the raw phdr payload."""
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        hdr = fh.read(8)
        if len(hdr) < 8:
            break
        fourcc = hdr[:4]
        (size,) = struct.unpack_from("<I", hdr, 4)
        if fourcc == b"phdr":
            if pos + 8 + size > end:
                warnings.append("phdr chunk overruns its LIST/pdta parent; truncated")
                size = max(0, end - pos - 8)
            if size > PHDR_CAP:
                # bag indices are u16, so no SoundFont has more than 65535 presets; a bigger
                # size is corruption, and fh.read() would preallocate it (up to 4 GiB) first
                raise SF2Error(f"phdr chunk is {size} bytes (more than {PHDR_CAP // PHDR_RECORD} records)")
            return _read_exact(fh, size, "phdr")
        pos += 8 + size + (size & 1)
    raise SF2Error("LIST/pdta has no phdr sub-chunk")


# --------------------------------------------------------------------------- per-file


def _empty_record(file: str, size: int) -> dict:
    return {
        "file": file,
        "bytes": size,
        "sha256": None,
        "ifil": None,
        "info": {k: None for k in INFO_KEYS},
        "melodic_bank0": 0,
        "has_drums": False,
        "banks": [],
        "preset_count": 0,
        "presets_by_bank": {},
        "parse_ok": False,
        "error": None,
        "warnings": [],
    }


def scan_file(path: str, file: str | None = None) -> dict:
    """Parse one SoundFont's headers (no sha256).  Never raises; see parse_ok/error."""
    file = file if file is not None else os.path.basename(path)
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        rec = _empty_record(file, 0)
        rec["error"] = f"stat failed: {exc}"
        return rec
    rec = _empty_record(file, size)
    try:
        with open(path, "rb") as fh:
            walked = _walk_riff(fh, size)
        presets = parse_phdr(walked["phdr"])
    except SF2Error as exc:
        rec["error"] = str(exc)
        return rec
    except (OSError, struct.error) as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
        return rec

    info = walked["info"]
    rec["ifil"] = info["ifil"]
    rec["info"] = info["fields"]
    by_bank: dict[int, int] = {}
    bank0_melodic: set[int] = set()
    for _name, preset, bank in presets:
        by_bank[bank] = by_bank.get(bank, 0) + 1
        if bank == 0 and preset <= 127:
            bank0_melodic.add(preset)
    banks = sorted(by_bank)
    rec["melodic_bank0"] = len(bank0_melodic)
    rec["has_drums"] = 128 in by_bank
    rec["banks"] = banks
    rec["preset_count"] = len(presets)
    rec["presets_by_bank"] = {str(b): by_bank[b] for b in banks}
    rec["parse_ok"] = True
    rec["warnings"] = walked["warnings"]
    if info["ifil"] is None:
        rec["warnings"].append("INFO has no ifil sub-chunk")
    return rec


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb", buffering=0) as fh:
        while True:
            block = fh.read(HASH_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- directory


def list_soundfonts(root: str) -> list[str]:
    """Return the sorted base names of every *.sf2 / *.SF2 (case-insensitive) in root.

    Raises ValueError for a name that is not valid UTF-8: os.listdir smuggles such bytes through
    as lone surrogates, which json.dump(ensure_ascii=False) cannot write — and it would only fail
    in write_json, after the sha256 pass over the whole collection. The name is the catalog's
    key for the font, so the owner has to rename the file; say so up front.
    """
    names = [n for n in os.listdir(root) if n.lower().endswith(".sf2") and os.path.isfile(os.path.join(root, n))]
    bad = []
    for n in names:
        try:
            n.encode("utf-8")
        except UnicodeEncodeError:
            bad.append(n.encode("utf-8", "surrogateescape").decode("utf-8", "replace"))
    if bad:
        raise ValueError(f"{len(bad)} file name(s) under {root} are not valid UTF-8 and cannot be catalogued; "
                         f"rename them: {sorted(bad)[:5]}")
    return sorted(names)


def scan_dir(root: str, limit: int | None = None, threads: int = DEFAULT_THREADS, do_hash: bool = True, log=None) -> dict:
    """Scan every SoundFont under root; returns the soundfonts.json document (a dict)."""
    log = log or (lambda *_: None)
    names = list_soundfonts(root)
    if limit is not None:
        names = names[:limit]
    t0 = time.monotonic()
    fonts = [scan_file(os.path.join(root, n), n) for n in names]
    t_parse = time.monotonic() - t0
    bad = [f for f in fonts if not f["parse_ok"]]
    log(f"parsed {len(fonts)} headers in {t_parse:.1f}s ({len(bad)} failed)")

    t_hash = 0.0
    if do_hash:
        t1 = time.monotonic()
        with ThreadPoolExecutor(max_workers=threads) as pool:
            digests = list(pool.map(lambda n: _safe_sha256(os.path.join(root, n)), names))
        for rec, digest in zip(fonts, digests):
            if isinstance(digest, Exception):
                rec["warnings"].append(f"sha256 failed: {digest}")
            else:
                rec["sha256"] = digest
        t_hash = time.monotonic() - t1
        total = sum(f["bytes"] for f in fonts)
        log(f"sha256 of {total / 2**30:.1f} GiB in {t_hash:.1f}s with {threads} threads")

    return {
        "schema": SCHEMA,
        "generated_at": now_iso(),
        "root": os.path.normpath(root),
        "count": len(fonts),
        "parse_failures": len(bad),
        "timing_s": {"parse": round(t_parse, 2), "sha256": round(t_hash, 2)},
        "fonts": fonts,
    }


def _safe_sha256(path: str):
    try:
        return sha256_file(path)
    except OSError as exc:  # pragma: no cover - disk errors
        return exc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default="soundfonts", help="directory holding *.sf2 (default: soundfonts)")
    ap.add_argument("--out", default="catalog/soundfonts.json", help="output JSON path")
    ap.add_argument("--limit", type=int, default=None, help="only scan the first N files (sorted by name)")
    ap.add_argument("--threads", type=int, default=DEFAULT_THREADS, help="sha256 worker threads (default 8)")
    ap.add_argument("--no-hash", action="store_true", help="skip sha256 (sha256 = null); for quick checks")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2
    log = (lambda *_: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))
    try:
        doc = scan_dir(args.root, limit=args.limit, threads=args.threads, do_hash=not args.no_hash, log=log)
    except ValueError as exc:   # un-catalogable file names (list_soundfonts); nothing was hashed yet
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write_json(doc, args.out)
    for rec in doc["fonts"]:
        if not rec["parse_ok"]:
            log(f"  FAILED {rec['file']}: {rec['error']}")
        for w in rec["warnings"]:
            log(f"  warn   {rec['file']}: {w}")
    log(f"wrote {args.out}: {doc['count']} fonts, {doc['parse_failures']} parse failures")
    return 1 if doc["parse_failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
