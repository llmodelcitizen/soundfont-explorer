"""Manifests (plan §9): /c/<hash>.json (catalog), /s/<song>/<hash>.json (set), /songs.json.

`sfr manifest` validates every done render for the selected songs, packs them (idempotent)
and writes the three documents. Content-addressed documents never contain timestamps.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from .config import Paths, RenderSettings
from .jobs import Job, now_iso, read_meta
from .pack import pack_song
from .validate import validate_job

SCHEMA = 1


def _dump(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _content_hash(blob: bytes, n: int = 12) -> str:
    return hashlib.sha256(blob).hexdigest()[:n]


def _write(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == blob:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(path)


def _sweep(dirpath: Path, keep: set[str]) -> None:
    """Remove stale content-addressed siblings so out/public mirrors the current state."""
    if not dirpath.exists():
        return
    for p in dirpath.iterdir():
        if p.is_file() and p.name not in keep:
            p.unlink()


FACET_KEYS = ("engine", "chip", "type", "completeness", "bank_map", "size", "lineage", "decade", "quality")


def build_catalog(variants: list[dict], engines_json: dict) -> dict:
    engines = []
    for eid, e in engines_json["engines"].items():
        if not e.get("version"):
            continue
        engines.append({"id": eid, "label": e.get("label"), "version": e.get("version"),
                        "commit": (e.get("commit") or "")[:7] or None, "url": e.get("url"),
                        "license": e.get("license")})
    facets: dict[str, dict[str, int]] = {k: {} for k in FACET_KEYS}
    out_variants = []
    for v in variants:
        f = dict(v.get("facets") or {})
        f.setdefault("engine", v["engine"])
        f.setdefault("chip", v.get("chip") or v.get("chip_family"))
        f.setdefault("type", v.get("type"))
        q = f.get("quality")
        for k in FACET_KEYS:
            val = f.get(k)
            vals = val if isinstance(val, list) else ([val] if val is not None else [])
            for x in vals:
                facets[k][str(x)] = facets[k].get(str(x), 0) + 1
        out_variants.append({
            "id": v["id"], "slug": v.get("slug"), "label": v.get("label"), "engine": v["engine"],
            "chip": v.get("chip") or v.get("chip_family"), "type": v.get("type"), "facets": f,
            "bank": v.get("bank"), "source": v.get("source"), "render": v.get("render"),
            "legal_note": v.get("legal_note"), "requires_rom": bool(v.get("requires_rom")),
            "aliases": v.get("aliases") or [],
        })
        _ = q
    return {"schema": SCHEMA, "engines": engines,
            "facets": {k: [{"value": val, "count": c} for val, c in sorted(facets[k].items())] for k in FACET_KEYS},
            "variants": out_variants}


def build_set(song: dict, settings: RenderSettings, order: list[str], groups: list[dict],
              variants_meta: dict[str, dict], excluded: list[dict]) -> dict:
    D = int(song["duration_s"])
    slot_of: dict[str, tuple[int, int]] = {}
    for gi, g in enumerate(groups):
        for slot, vid in enumerate(g["variants"]):
            slot_of[vid] = (gi, slot)
    vs = {}
    for vid, m in variants_meta.items():
        gi, slot = slot_of[vid]
        vs[vid] = {"render_hash": m["render_hash"], "lufs": round(float(m["lufs"]), 2),
                   "gain_db": round(float(m["gain_db"]), 2), "tp": round(float(m["tp"]), 2),
                   "group": gi, "slot": slot}
    return {
        "schema": SCHEMA, "song": song["id"], "sr": settings.sample_rate, "duration_s": D,
        "slice_s": settings.slice_s, "slices": settings.n_slices(D),
        "lead_in_s": settings.lead_in_s, "lead_out_s": settings.lead_out_s,
        "segment_samples": settings.segment_samples,
        "listen": {"slice_s": settings.listen_slice_s, "slices": settings.n_listen(D),
                   "bitrate": settings.listen_bitrate_kbps, "segment_samples": settings.listen_segment_samples},
        "scrub": {"bitrate": settings.scrub_bitrate_kbps, "pack_size": settings.pack_size},
        "lufs_target": settings.lufs_target, "order": order, "groups": groups, "variants": vs,
        "excluded": excluded,
    }


def build_manifests(paths: Paths, songs: list[dict], variants: list[dict], settings: RenderSettings,
                    engines_json: dict, jobs_by_song: dict[str, list[Job]], *, thorough: bool = False,
                    defaults: dict | None = None, echo=print) -> dict:
    public = paths.public
    catalog = build_catalog(variants, engines_json)
    cblob = _dump(catalog)
    chash = _content_hash(cblob)
    _write(public / "c" / f"{chash}.json", cblob)
    _sweep(public / "c", {f"{chash}.json"})

    song_entries = []
    report: dict[str, Any] = {"catalog": f"/c/{chash}.json", "songs": {}}
    for song in songs:
        sid = song["id"]
        jobs = jobs_by_song.get(sid, [])
        ok_meta: dict[str, dict] = {}
        excluded: list[dict] = []
        for job in jobs:
            v = validate_job(job, paths, thorough=thorough)
            if v.ok:
                ok_meta[job.variant_id] = read_meta(job.meta_path(paths)) or {}
            else:
                if v.reason not in ("no-meta",):
                    excluded.append({"id": job.variant_id, "reason": v.reason})
        # canonical order = catalog order filtered to ok renders
        order = [v["id"] for v in variants if v["id"] in ok_meta]
        if not order:
            echo(f"[manifest] {sid}: no valid renders, skipping")
            report["songs"][sid] = {"variants": 0, "excluded": len(excluded)}
            continue
        vmap = {v["id"]: v for v in variants}
        render_dirs = {vid: paths.render_dir(sid, vid) for vid in order}
        render_hashes = {vid: ok_meta[vid]["render_hash"] for vid in order}
        D = int(song["duration_s"])
        packed = pack_song(sid, [vmap[v] for v in order], render_dirs, render_hashes,
                           settings.n_slices(D), settings.n_listen(D), public, settings.pack_size)
        sset = build_set(song, settings, order, packed["groups"], ok_meta, excluded)
        sblob = _dump(sset)
        shash = _content_hash(sblob)
        _write(public / "s" / sid / f"{shash}.json", sblob)
        _sweep(public / "s" / sid, {f"{shash}.json"})
        # prune pack groups that are no longer referenced
        live = {g["hash"] for g in packed["groups"]}
        gdir = public / "a" / sid / "g"
        if gdir.exists():
            for p in gdir.iterdir():
                if p.is_dir() and p.name not in live:
                    shutil.rmtree(p)
        live_l = set(render_hashes.values())
        ldir = public / "a" / sid / "l"
        if ldir.exists():
            for p in ldir.iterdir():
                if p.is_dir() and p.name not in live_l:
                    shutil.rmtree(p)
        song_entries.append({
            "id": sid, "title": song.get("title"), "composer": song.get("composer"),
            "sequencer": song.get("sequencer"), "source_url": song.get("source_url"),
            "license": song.get("license"), "modifications": song.get("modifications") or "none",
            "duration_s": D, "variant_count": len(order), "set": f"/s/{sid}/{shash}.json",
        })
        report["songs"][sid] = {"variants": len(order), "excluded": len(excluded), "set": f"/s/{sid}/{shash}.json",
                               "groups": len(packed["groups"]),
                               "excluded_reasons": sorted({e["reason"].split(":")[0] for e in excluded})}
        echo(f"[manifest] {sid}: {len(order)} variants, {len(packed['groups'])} groups, {len(excluded)} excluded")

    defaults = defaults or {}
    default_song = defaults.get("song") or next((s["id"] for s in songs if s.get("default")), None) \
        or (song_entries[0]["id"] if song_entries else None)
    songs_json = {"schema": SCHEMA, "generated_at": now_iso(), "catalog": f"/c/{chash}.json",
                  "defaults": {"song": default_song, "variant": defaults.get("variant", "adl-b58")},
                  "songs": song_entries}
    _write(public / "songs.json", json.dumps(songs_json, indent=1, sort_keys=True, ensure_ascii=False).encode())
    report["songs_json"] = str(public / "songs.json")
    return report
