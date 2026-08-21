#!/usr/bin/env python3
"""Build / inspect SFPK v1 packs (plan section 8.4) from Opus segments. Stdlib only.

Layout (little-endian, unaligned):
    "SFPK" | u8 version = 1 | u8 count n | u16 reserved = 0 | u32le len[n] | member bytes concatenated
Member j starts at 8 + 4*n + sum(len[0..j)).

CLI examples:
    # one pack = slice 0003 of every variant dir under work/derisk/e1m1 (max 24, cycled up to --fill)
    python3 mkpack.py --slice 3 --variants work/derisk/e1m1/* --fill 24 --out web/test/proto/packs/0003.pk
    # explicit member list
    python3 mkpack.py --out x.pk a.opus b.opus ...
    # inspect
    python3 mkpack.py --info x.pk
"""
import argparse
import json
import os
import struct
import sys

MAGIC = b"SFPK"
VERSION = 1
MAX_MEMBERS = 255


def build_pack(members):
    """members: list of bytes -> pack bytes."""
    n = len(members)
    if not 1 <= n <= MAX_MEMBERS:
        raise ValueError("member count must be 1..%d, got %d" % (MAX_MEMBERS, n))
    header = MAGIC + struct.pack("<BBH", VERSION, n, 0)
    lens = struct.pack("<%dI" % n, *[len(m) for m in members])
    return header + lens + b"".join(members)


def parse_header(data):
    """Parse just the fixed header + length table (data may be a prefix of the pack).
    Returns (count, [len...], offsets) where offsets[j] is the absolute byte offset of member j."""
    if len(data) < 8 or data[:4] != MAGIC:
        raise ValueError("not an SFPK pack")
    version, n, reserved = struct.unpack("<BBH", data[4:8])
    if version != VERSION:
        raise ValueError("unsupported SFPK version %d" % version)
    if reserved != 0:
        raise ValueError("reserved field must be 0")
    need = 8 + 4 * n
    if len(data) < need:
        raise ValueError("truncated length table: need %d bytes, have %d" % (need, len(data)))
    lens = list(struct.unpack("<%dI" % n, data[8:need]))
    offsets, pos = [], need
    for ln in lens:
        offsets.append(pos)
        pos += ln
    return n, lens, offsets


def header_size(n):
    return 8 + 4 * n


def parse_pack(data):
    """Full pack bytes -> list of member bytes (validates total length)."""
    n, lens, offsets = parse_header(data)
    total = header_size(n) + sum(lens)
    if len(data) != total:
        raise ValueError("pack length %d != expected %d" % (len(data), total))
    return [data[o:o + ln] for o, ln in zip(offsets, lens)]


def member_range(header_bytes, j):
    """(start, end_inclusive) byte range of member j, for an HTTP Range request."""
    n, lens, offsets = parse_header(header_bytes)
    if not 0 <= j < n:
        raise IndexError(j)
    return offsets[j], offsets[j] + lens[j] - 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="member files in order")
    ap.add_argument("--out", help="output .pk path")
    ap.add_argument("--variants", nargs="*", default=[], help="variant dirs containing seg/NNNN.opus")
    ap.add_argument("--slice", type=int, default=0, help="slice index to take from each variant dir")
    ap.add_argument("--fill", type=int, default=0, help="cycle members round-robin until the pack has this many")
    ap.add_argument("--info", help="print header info of an existing pack as JSON and exit")
    a = ap.parse_args(argv)

    if a.info:
        with open(a.info, "rb") as f:
            data = f.read()
        n, lens, offsets = parse_header(data)
        print(json.dumps({"count": n, "lens": lens, "offsets": offsets, "header_bytes": header_size(n),
                          "total_bytes": len(data), "consistent": len(data) == header_size(n) + sum(lens)}))
        return 0

    paths = list(a.files)
    for vd in a.variants:
        p = os.path.join(vd, "seg", "%04d.opus" % a.slice)
        if os.path.isfile(p):
            paths.append(p)
        else:
            print("warning: missing %s (skipped)" % p, file=sys.stderr)
    if not paths:
        ap.error("no members")
    if a.fill and len(paths) < a.fill:
        base = list(paths)
        i = 0
        while len(paths) < a.fill:
            paths.append(base[i % len(base)])
            i += 1
    paths = paths[:MAX_MEMBERS]
    members = []
    for p in paths:
        with open(p, "rb") as f:
            members.append(f.read())
    pack = build_pack(members)
    if not a.out:
        ap.error("--out required")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "wb") as f:
        f.write(pack)
    print("wrote %s: %d members, %d bytes (header %d)" % (a.out, len(members), len(pack), header_size(len(members))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
