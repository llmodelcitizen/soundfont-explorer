"""Published-site operations: overview, remove a track, prune — always against S3 listings.

The site bucket is the only truth about what is published. Local out/public on any host is
just a working copy (the workstation's is already missing cloud-published audio), so every keep/
delete decision here starts from list_objects_v2, and songs.json is rebuilt via
`sfr manifest --songs-json-only` after syncing /s and /c down WITH --delete.

Prune keeps exactly what the live songs.json references: each song's current set doc plus
the listen (render_hash) and scrub (group hash) prefixes that doc names. It refuses to run
while a render run is live (shards publish new sets mid-run) and only touches a/ and s/ —
catalog docs are a few KB and stay.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

from .config import get_config

IMMUTABLE = "public,max-age=31536000,immutable"
SHORT = "public,max-age=60,stale-while-revalidate=600"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- S3 + shell primitives
# Thin module-level wrappers: the stdlib-only unit tests (no boto3, no aws CLI in CI) stub
# these and drive the real ordering + drop-guard logic built on top of them.

def _list(prefix: str) -> list[dict]:
    cfg = get_config()
    out: list[dict] = []
    for page in cfg.s3.get_paginator("list_objects_v2").paginate(
            Bucket=cfg.site_bucket, Prefix=prefix):
        out += page.get("Contents", [])
    return out


def _delete_keys(keys: list[str]) -> int:
    cfg = get_config()
    for i in range(0, len(keys), 1000):
        cfg.s3.delete_objects(Bucket=cfg.site_bucket,
                              Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                                      "Quiet": True})
    return len(keys)


def live_songs_json() -> dict:
    cfg = get_config()
    r = cfg.s3.get_object(Bucket=cfg.site_bucket, Key="songs.json")
    return json.load(r["Body"])


def sync_down() -> None:
    """Mirror the bucket's /s and /c into the snapshot's out/public (bucket is truth)."""
    cfg = get_config()
    out_public = os.path.join(cfg.repo, "out", "public")
    os.makedirs(out_public, exist_ok=True)
    for pre in ("s", "c"):
        subprocess.run(["aws", "s3", "sync", f"s3://{cfg.site_bucket}/{pre}/",
                        os.path.join(out_public, pre) + "/", "--delete", "--only-show-errors"],
                       check=True)


def _run_manifest() -> dict:
    """`sfr manifest --songs-json-only` in the snapshot: rewrites out/public/songs.json from
    the set docs under out/public/s (a song without one is not listed); returns the report."""
    cfg = get_config()
    p = subprocess.run([sys.executable, "-m", "sfr", "manifest", "--songs-json-only",
                        "--default-song", "freedoom-e1m1", "--default-variant", "adl-b58"],
                       cwd=os.path.join(cfg.repo, "render"), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"manifest --songs-json-only failed: {p.stderr[-1500:]}")
    return json.loads(p.stdout)


def _publish_songs_json() -> None:
    """Upload out/public/c/*.json + songs.json to the site bucket and invalidate /songs.json."""
    cfg = get_config()
    out_public = os.path.join(cfg.repo, "out", "public")
    subprocess.run(["aws", "s3", "sync", os.path.join(out_public, "c") + "/",
                    f"s3://{cfg.site_bucket}/c/", "--only-show-errors",
                    "--exclude", "*", "--include", "*.json",
                    "--content-type", "application/json",
                    "--cache-control", IMMUTABLE, "--size-only"], check=True)
    subprocess.run(["aws", "s3", "cp", os.path.join(out_public, "songs.json"),
                    f"s3://{cfg.site_bucket}/songs.json", "--only-show-errors",
                    "--content-type", "application/json", "--cache-control", SHORT], check=True)
    if cfg.distribution:
        subprocess.run(["aws", "cloudfront", "create-invalidation", "--distribution-id",
                        cfg.distribution, "--paths", "/songs.json"],
                       check=True, capture_output=True)


# ---------------------------------------------------------------- rebuild + publish

def _expected_absent() -> set[str]:
    """Ids whose absence from a rebuilt songs.json is an editorial choice: library entries
    hidden by the admin (the fragment omits them)."""
    try:
        from .library import get_library  # noqa: PLC0415  (no import cycle: library never imports us)
        return {e["id"] for e in get_library().entries() if e.get("hidden")}
    except Exception:
        return set()


def rebuild_and_publish(expect_dropped: frozenset[str] = frozenset()) -> dict:
    """sfr manifest --songs-json-only from the synced sets, then publish songs.json +
    catalog docs and invalidate. The caller must sync_down() first."""
    cfg = get_config()
    out_public = os.path.join(cfg.repo, "out", "public")
    try:
        live_ids = {s["id"] for s in live_songs_json().get("songs", [])}
    except Exception:
        live_ids = set()
    report = _run_manifest()
    # A rebuild can only list songs the snapshot's corpus knows. If the live songs.json has
    # ids this one would lose — beyond an explicit remove (expect_dropped) or a hidden
    # library entry — that is a corpus gap on the box (the 2026-08-24 private-starwars
    # incident: songs/private was missing from the bundle), not an editorial choice:
    # publishing would silently drop live tracks and prime prune to delete their audio.
    with open(os.path.join(out_public, "songs.json")) as fh:
        new_ids = {s["id"] for s in json.load(fh)["songs"]}
    dropped = sorted(live_ids - new_ids - set(expect_dropped) - _expected_absent())
    if dropped:
        raise RuntimeError(
            f"refusing to publish: rebuild would drop live tracks {dropped[:8]}"
            + ("…" if len(dropped) > 8 else "")
            + " — remove them deliberately (Published tab), hide them in the Library, "
              "or fix the box's corpus first")
    _publish_songs_json()
    return report


# ---------------------------------------------------------------- overview

def overview() -> dict:
    """Live songs.json ⨝ S3 listings, with a discrepancy report."""
    try:
        live = live_songs_json()
    except Exception:
        live = {"songs": []}
    by_id = {s["id"]: s for s in live.get("songs", [])}
    audio: dict[str, dict] = {}
    for o in _list("a/"):
        sid = o["Key"].split("/")[1]
        a = audio.setdefault(sid, {"objects": 0, "bytes": 0})
        a["objects"] += 1
        a["bytes"] += o["Size"]
    sets: dict[str, int] = {}
    for o in _list("s/"):
        sid = o["Key"].split("/")[1]
        sets[sid] = sets.get(sid, 0) + 1
    tracks = []
    for sid in sorted(set(by_id) | set(audio) | set(sets)):
        e = by_id.get(sid)
        tracks.append({
            "id": sid,
            "title": e["title"] if e else None,
            "path": (e or {}).get("path"),
            "variant_count": e["variant_count"] if e else None,
            "duration_s": e["duration_s"] if e else None,
            "audio_objects": audio.get(sid, {}).get("objects", 0),
            "audio_bytes": audio.get(sid, {}).get("bytes", 0),
            "set_docs": sets.get(sid, 0),
            "in_songs_json": e is not None,
        })
    problems = [t["id"] for t in tracks
                if t["in_songs_json"] != (t["audio_objects"] > 0 and t["set_docs"] > 0)]
    return {"generated_at": live.get("generated_at"), "tracks": tracks, "discrepancies": problems}


# ---------------------------------------------------------------- remove + prune

def remove_track(sid: str) -> dict:
    """Delete /a/<id> and /s/<id> entirely, then rebuild + publish songs.json without it."""
    keys = [o["Key"] for o in _list(f"a/{sid}/")] + [o["Key"] for o in _list(f"s/{sid}/")]
    n = _delete_keys(keys)
    sync_down()
    report = rebuild_and_publish(expect_dropped=frozenset({sid}))
    return {"id": sid, "deleted_objects": n, "songs_json": report.get("songs_json")}


def prune(dry_run: bool = True) -> dict:
    """Delete a/ and s/ objects the live songs.json no longer references."""
    cfg = get_config()
    live = live_songs_json()  # no fallback: pruning without a songs.json would keep nothing
    keep: set[str] = set()
    keep_prefixes: list[str] = []
    for song in live.get("songs", []):
        set_key = song["set"].lstrip("/")
        keep.add(set_key)
        doc = json.load(cfg.s3.get_object(Bucket=cfg.site_bucket, Key=set_key)["Body"])
        sid = doc["song"]
        for g in doc.get("groups", []):
            keep_prefixes.append(f"a/{sid}/g/{g['hash']}/")
        for v in doc.get("variants", {}).values():
            keep_prefixes.append(f"a/{sid}/l/{v['render_hash']}/")
    kp = tuple(keep_prefixes)
    doomed = [o["Key"] for o in _list("a/") + _list("s/")
              if o["Key"] not in keep and not o["Key"].startswith(kp)]
    by_prefix: dict[str, int] = {}
    for k in doomed:
        p = "/".join(k.split("/")[:3])
        by_prefix[p] = by_prefix.get(p, 0) + 1
    report = {"dry_run": dry_run, "checked_at": _now(),
              "kept_songs": len(live.get("songs", [])),
              "doomed_objects": len(doomed),
              "doomed_by_prefix": dict(sorted(by_prefix.items(), key=lambda x: -x[1])[:40])}
    if not dry_run and doomed:
        report["deleted"] = _delete_keys(doomed)
    return report
