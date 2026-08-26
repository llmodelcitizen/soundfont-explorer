"""Published-site operations: overview, remove a track, prune — always against S3 listings.

The site bucket is the only truth about what is published. Local out/public on any host is
just a working copy (the workstation's is already missing cloud-published audio), so every keep/
delete decision here starts from list_objects_v2, and songs.json is rebuilt via
`sfr manifest --songs-json-only` after syncing /s and /c down WITH --delete.

Prune keeps exactly what the live songs.json references: each song's current set doc plus
the listen (render_hash) and scrub (group hash) prefixes that doc names. It refuses to run
while a render run is live (shards publish new sets mid-run) and only touches a/ and s/ —
catalog docs are a few KB and stay.

overview() is the only read-only view here and the only caller allowed a cheap listing: it
asks S3 for the child prefixes of a/ (one delimited call, one result per song) rather than
walking the millions of objects underneath them (#46). prune() and remove_track() delete,
so they keep listing in full and must: a delimited listing cannot see a superseded render
sitting inside a song that also has current ones, and a delete decided from one would
either miss it or take the whole song with it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading

from .clock import now_iso
from .config import get_config

IMMUTABLE = "public,max-age=31536000,immutable"
SHORT = "public,max-age=60,stale-while-revalidate=600"

# Everything here works through the snapshot's out/public and then the bucket, and takes
# minutes (an aws s3 sync plus an sfr manifest over the corpus). FastAPI serves these sync
# routes on a threadpool and the render finisher syncs from its own watcher thread, so a
# second caller could otherwise re-mirror out/public in the middle of a remove and restore
# the very set doc it just dropped (#15). Re-entrant: remove_track() and resync_and_publish()
# hold it across their own calls to sync_down()/rebuild_and_publish(). prune() stays outside
# it — it decides from the bucket alone, never from out/public.
OPS_LOCK = threading.RLock()


class NoSongsJson(RuntimeError):
    """The site bucket has no songs.json at all (a fresh site) — distinct from an S3 error
    reading it, which callers must not mistake for 'nothing is live'."""


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


def _list_delimited(prefix: str) -> list[str]:
    """What sits immediately under `prefix`: the child prefixes ("a/<id>/"), plus any stray
    key at that level.

    One delimited list_objects_v2 — S3 rolls each song up into a single CommonPrefix instead
    of returning its contents, so "which songs have audio at all" costs ~0.6s for 188 songs
    where descending into them was ~2.4M objects and minutes (#46).

    Read-only callers only; presence is all this can answer. A key sitting directly under
    `prefix` has no delimiter after it and so never becomes a CommonPrefix — it comes back in
    Contents on the same page, and is kept here so the id set matches exactly what the full
    walk used to derive.
    """
    cfg = get_config()
    out: list[str] = []
    for page in cfg.s3.get_paginator("list_objects_v2").paginate(
            Bucket=cfg.site_bucket, Prefix=prefix, Delimiter="/"):
        out += [c["Prefix"] for c in page.get("CommonPrefixes", [])]
        out += [o["Key"] for o in page.get("Contents", [])]
    return out


def _delete_keys(keys: list[str]) -> int:
    cfg = get_config()
    for i in range(0, len(keys), 1000):
        cfg.s3.delete_objects(Bucket=cfg.site_bucket,
                              Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                                      "Quiet": True})
    return len(keys)


def _get_json(key: str) -> dict | None:
    """The JSON object at `key`, or None when there is no such object."""
    cfg = get_config()
    try:
        r = cfg.s3.get_object(Bucket=cfg.site_bucket, Key=key)
    except cfg.s3.exceptions.NoSuchKey:
        return None
    return json.load(r["Body"])


def live_songs_json() -> dict:
    doc = _get_json("songs.json")
    if doc is None:
        raise NoSongsJson("no songs.json in the site bucket")
    return doc


def sync_down() -> None:
    """Mirror the bucket's /s and /c into the snapshot's out/public (bucket is truth)."""
    cfg = get_config()
    out_public = os.path.join(cfg.repo, "out", "public")
    os.makedirs(out_public, exist_ok=True)
    with OPS_LOCK:
        for pre in ("s", "c"):
            subprocess.run(["aws", "s3", "sync", f"s3://{cfg.site_bucket}/{pre}/",
                            os.path.join(out_public, pre) + "/", "--delete",
                            "--only-show-errors"], check=True)


def _run_manifest() -> dict:
    """`sfr manifest --songs-json-only` in the snapshot: rewrites out/public/songs.json from
    the set docs under out/public/s (a song without one is not listed); returns the report."""
    cfg = get_config()
    # --default-song must match songs/tools/canon.py DEFAULT_SONG_ID; --default-variant is
    # 8MbGM_Enhanced18 (render/sfr/cli.py's default)
    p = subprocess.run([sys.executable, "-m", "sfr", "manifest", "--songs-json-only",
                        "--default-song", "starwars", "--default-variant", "sf2-2ef5bd3eb3"],
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


def rebuild_and_publish(require_dropped: frozenset[str] = frozenset()) -> dict:
    """sfr manifest --songs-json-only from the synced sets, then publish songs.json +
    catalog docs and invalidate. The caller must sync_down() first.

    `require_dropped` are ids the caller is deliberately removing: they may vanish from the
    rebuilt songs.json (the drop guard ignores them) and they MUST — an id that is still
    listed means the removal did not take, and publishing that songs.json would clear the
    caller to delete objects it still points at (#15)."""
    cfg = get_config()
    out_public = os.path.join(cfg.repo, "out", "public")
    with OPS_LOCK:
        try:
            live_ids = {s["id"] for s in live_songs_json().get("songs", [])}
        except NoSongsJson:
            live_ids = set()   # a site that has never published: nothing can be dropped
        # Only "there is no songs.json" is benign. A transient S3 error must NOT read as an
        # empty live set: that silently disables the drop guard below, which is the only
        # thing between a corpus gap and a remove deleting a live track's audio.
        report = _run_manifest()
        # A rebuild can only list songs the snapshot's corpus knows. If the live songs.json has
        # ids this one would lose — beyond an explicit remove (require_dropped) or a hidden
        # library entry — that is a corpus gap on the box (the 2026-08-24 private-starwars
        # incident: songs/private was missing from the bundle), not an editorial choice:
        # publishing would silently drop live tracks and prime prune to delete their audio.
        with open(os.path.join(out_public, "songs.json")) as fh:
            new_ids = {s["id"] for s in json.load(fh)["songs"]}
        dropped = sorted(live_ids - new_ids - set(require_dropped) - _expected_absent())
        if dropped:
            raise RuntimeError(
                f"refusing to publish: rebuild would drop live tracks {dropped[:8]}"
                + ("…" if len(dropped) > 8 else "")
                + " — remove them deliberately (Published tab), hide them in the Library, "
                  "or fix the box's corpus first")
        # The other direction, which the guard above cannot see (an expected drop that did
        # not happen is not a drop): a set doc restored under out/public/s by a concurrent
        # sync_down() — the render finisher, a second Remove, a /rebuild — puts the id right
        # back into the manifest, and publishing it while the caller goes on to delete the
        # objects is exactly the 404s of #15. Refuse instead; the operator can retry.
        still = sorted(set(require_dropped) & new_ids)
        if still:
            raise RuntimeError(
                f"refusing to publish: the rebuilt songs.json still lists {still} — its set "
                "docs came back (a concurrent sync or rebuild?); nothing was published")
        _publish_songs_json()
    return report


def resync_and_publish() -> dict:
    """Mirror the bucket into out/public, then rebuild + publish songs.json from it.

    The pair has to be atomic: between the two, another caller's sync or remove can
    rewrite out/public under us (a remove's rmtree'd set doc coming back, or a set doc
    it just dropped disappearing from a mirror this rebuild is about to publish), so
    both run under OPS_LOCK. Callers that need the pair should use this rather than
    calling sync_down() and rebuild_and_publish() themselves."""
    with OPS_LOCK:
        sync_down()
        return rebuild_and_publish()


# ---------------------------------------------------------------- overview

def _checked_id(sid: str) -> str:
    """A track id straight off a URL path segment, for everything that turns one into a
    filesystem path or an S3 prefix (_local_set_dir's result gets rmtree'd), so anything but
    a single path component is refused."""
    if not sid or sid in (".", "..") or "/" in sid or "\\" in sid:
        raise ValueError(f"bad track id {sid!r}")
    return sid


def overview() -> dict:
    """Live songs.json ⨝ S3 listings, with a discrepancy report.

    Presence, not weight: this asks which song prefixes exist under a/, never what is inside
    them. Per-song object counts and byte totals are deliberately NOT here — audio_usage()
    measures one song when the UI asks. Computing them for every song is what made the tab
    unusable (#46): ~8-16k objects per song across 188 songs is ~2.4M objects, ~2,400
    sequential list pages, 4-12 minutes on every single load — so the tab never finished
    painting and its Republish songs.json button, the only manual rebuild there is, could
    not be reached. s/ stays a full walk: it is one object per published set doc, one page.
    """
    try:
        live = live_songs_json()
    except NoSongsJson:
        live = {"songs": []}   # a fresh site: the listings below are the whole story
    # Any other read failure propagates: "songs.json says nothing is published" would flag
    # every track as a discrepancy and invite a Remove that deletes live audio.
    by_id = {s["id"]: s for s in live.get("songs", [])}
    # "a/<id>/" -> "<id>"; a bare "a/" directory marker yields "" and is not a song.
    audio = {p.split("/")[1] for p in _list_delimited("a/")} - {""}
    sets: dict[str, int] = {}
    for o in _list("s/"):
        sid = o["Key"].split("/")[1]
        sets[sid] = sets.get(sid, 0) + 1
    tracks = []
    for sid in sorted(set(by_id) | audio | set(sets)):
        e = by_id.get(sid)
        tracks.append({
            "id": sid,
            "title": e["title"] if e else None,
            "path": (e or {}).get("path"),
            "variant_count": e["variant_count"] if e else None,
            "duration_s": e["duration_s"] if e else None,
            "has_audio": sid in audio,
            "set_docs": sets.get(sid, 0),
            "in_songs_json": e is not None,
        })
    problems = [t["id"] for t in tracks
                if t["in_songs_json"] != (t["has_audio"] and t["set_docs"] > 0)]
    return {"generated_at": live.get("generated_at"), "tracks": tracks, "discrepancies": problems}


def audio_usage(sid: str) -> dict:
    """What one song's audio actually costs: every object under a/<id>/, counted for real.

    Split out of overview() (#46). A single song is ~8-16k objects and a couple of seconds —
    fine on demand for the one row the operator opened, or the track they are about to
    Remove — where all 188 at once is minutes.

    Deliberately not cached. There is no cheaper way to refresh this than the same walk, so a
    cache would only ever serve a figure of unknown age, and every caller would then have to
    say how old it is or risk passing it off as live (the Remove confirmation quotes it).
    Measuring when asked keeps that question from existing; `measured_at` records when.
    """
    sid = _checked_id(sid)
    objects = _list(f"a/{sid}/")
    return {"id": sid, "objects": len(objects),
            "bytes": sum(o["Size"] for o in objects), "measured_at": now_iso()}


# ---------------------------------------------------------------- remove + prune

def _local_set_dir(sid: str) -> str:
    """out/public/s/<id> in the snapshot, for an id that has passed _checked_id."""
    return os.path.join(get_config().repo, "out", "public", "s", _checked_id(sid))


def remove_track(sid: str) -> dict:
    """Rebuild + publish songs.json without <id>, THEN delete /a/<id> and /s/<id>.

    The objects go last (#15): a refused rebuild (the drop guard firing on a corpus gap for
    some *other* live track) or a failed publish must leave the site exactly as it was — a
    live songs.json pointing at audio that is already gone 404s for visitors, and wedges
    the Published tab (every rebuild keeps refusing, prune trips on the missing set doc).
    sync_down mirrors the bucket, where the track's set docs still exist at this point, so
    they are dropped from the local mirror by hand before the manifest runs; the bucket is
    only touched once the new songs.json is live. Because that removal now rides on a local
    edit, rebuild_and_publish(require_dropped=…) re-checks the rebuilt songs.json and refuses
    if <id> is back — the whole thing runs under OPS_LOCK so a concurrent sync cannot get in.
    """
    set_dir = _local_set_dir(sid)
    with OPS_LOCK:
        sync_down()
        if os.path.isdir(set_dir):
            shutil.rmtree(set_dir)   # must not fail quietly: a surviving set doc keeps <id> listed
        report = rebuild_and_publish(require_dropped=frozenset({sid}))
        # Listed only now: the sync + manifest + publish above take minutes, and the delete
        # should cover anything written under the track's prefixes in that window too.
        keys = [o["Key"] for o in _list(f"a/{sid}/")] + [o["Key"] for o in _list(f"s/{sid}/")]
        try:
            n = _delete_keys(keys)
        except Exception as e:
            # songs.json is already live without <id>: the track IS gone from the site and
            # only orphaned objects remain, so don't report this as "remove failed".
            raise RuntimeError(
                f"{sid} is no longer published (songs.json is live without it), but deleting "
                f"its {len(keys)} objects failed: {e} — run Prune to clear them") from e
    return {"id": sid, "deleted_objects": n, "songs_json": report.get("songs_json")}


def prune(dry_run: bool = True) -> dict:
    """Delete a/ and s/ objects the live songs.json no longer references."""
    live = live_songs_json()  # no fallback: pruning without a songs.json would keep nothing
    keep: set[str] = set()
    keep_prefixes: list[str] = []
    missing_sets: list[str] = []
    for song in live.get("songs", []):
        set_key = song["set"].lstrip("/")
        keep.add(set_key)
        doc = _get_json(set_key)
        if doc is None:
            # songs.json names a set doc the bucket no longer has (a remove that died
            # half-way before #15, or a stray delete). Without the doc there is no way to
            # tell which of the song's audio is current, so keep all of it and say so —
            # Remove on the Published tab is the deliberate way to clear the track. The
            # s/ prefix is kept too: a superseded set doc may be the only surviving record
            # of which renders are current, and deleting it forecloses the repair.
            missing_sets.append(set_key)
            keep_prefixes.append(f"a/{song['id']}/")
            keep_prefixes.append(f"s/{song['id']}/")
            continue
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
    report = {"dry_run": dry_run, "checked_at": now_iso(),
              "kept_songs": len(live.get("songs", [])),
              "missing_sets": missing_sets,
              "doomed_objects": len(doomed),
              "doomed_by_prefix": dict(sorted(by_prefix.items(), key=lambda x: -x[1])[:40])}
    if not dry_run and doomed:
        report["deleted"] = _delete_keys(doomed)
    return report
