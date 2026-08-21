"""SFPK v1 packs and the per-song public audio layout (plan §8.4).

SFPK v1: "SFPK" · u8 version=1 · u8 count n · u16 reserved · u32le len[n] · members (unaligned).
Member j starts at 8 + 4n + Σ len[0..j).

Layout under out/public/a/<song>/:
  g/<group_hash>/<slice:04d>.pk    — slice i of the ≤24 variants of a group
  l/<render_hash>/<k:03d>.opus     — listen tier, hard-linked from work/
"""
from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path
from typing import Sequence

MAGIC = b"SFPK"
VERSION = 1


def pack_bytes(members: Sequence[bytes]) -> bytes:
    n = len(members)
    if not 1 <= n <= 255:
        raise ValueError("pack needs 1..255 members")
    head = MAGIC + struct.pack("<BBH", VERSION, n, 0) + struct.pack(f"<{n}I", *(len(m) for m in members))
    return head + b"".join(members)


def unpack_bytes(blob: bytes) -> list[bytes]:
    if blob[:4] != MAGIC:
        raise ValueError("not an SFPK")
    version, n, _res = struct.unpack_from("<BBH", blob, 4)
    if version != VERSION:
        raise ValueError(f"unsupported SFPK version {version}")
    lens = struct.unpack_from(f"<{n}I", blob, 8)
    off = 8 + 4 * n
    out = []
    for ln in lens:
        out.append(blob[off:off + ln])
        off += ln
    if off != len(blob):
        raise ValueError("trailing bytes / truncated pack")
    return out


def header_size(n: int) -> int:
    return 8 + 4 * n


def group_hash(render_hashes: Sequence[str]) -> str:
    return hashlib.sha256("".join(render_hashes).encode()).hexdigest()[:12]


def groups_of(items: Sequence, size: int) -> list[list]:
    return [list(items[i:i + size]) for i in range(0, len(items), size)]


def _link_or_copy(src: Path, dst: Path) -> None:
    if dst.exists():
        if dst.stat().st_size == src.stat().st_size:
            return
        dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        import shutil
        shutil.copy2(src, dst)


def pack_song(song_id: str, variants: Sequence[dict], render_dirs: dict[str, Path], render_hashes: dict[str, str],
              n_slices: int, n_listen: int, public: Path, pack_size: int = 24) -> dict:
    """variants: published+done variants in canonical order. Returns the `groups` block for the set manifest."""
    base = public / "a" / song_id
    groups = []
    for gi, grp in enumerate(groups_of(list(variants), pack_size)):
        ids = [v["id"] for v in grp]
        gh = group_hash([render_hashes[i] for i in ids])
        gdir = base / "g" / gh
        gdir.mkdir(parents=True, exist_ok=True)
        for i in range(n_slices):
            out = gdir / f"{i:04d}.pk"
            members = []
            for vid in ids:
                members.append((render_dirs[vid] / "seg" / f"{i:04d}.opus").read_bytes())
            blob = pack_bytes(members)
            if out.exists() and out.stat().st_size == len(blob):
                continue
            tmp = out.with_suffix(".pk.tmp")
            tmp.write_bytes(blob)
            tmp.replace(out)
        groups.append({"hash": gh, "variants": ids})
        for slot, vid in enumerate(ids):
            ldir = base / "l" / render_hashes[vid]
            for k in range(n_listen):
                _link_or_copy(render_dirs[vid] / "listen" / f"{k:03d}.opus", ldir / f"{k:03d}.opus")
    return {"groups": groups}
