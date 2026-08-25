"""Upload out/public to S3 and invalidate songs.json (plan §8.4). Every call is a real,
billable action: always run with --dry-run first."""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from .config import Paths

IMMUTABLE = "public,max-age=31536000,immutable"
SONGS_CC = "public,max-age=60,stale-while-revalidate=600"

# (top-level prefix, filename glob, Content-Type) for every kind of immutable object under
# out/public; each is served with IMMUTABLE. This is the one table every publisher derives its
# headers from — `sfr publish` (aws s3 sync), the cloud shards (s5cmd sync, render/cloud/shard.py)
# and `sfr publish --restamp` — so they cannot drift (#14: the shards used to upload bare, and
# CloudFront held their audio for a day instead of a year, under whatever type /etc/mime.types
# guessed — application/x-tex-pk for a .pk). The client never looks at the Content-Type
# (fetch → arrayBuffer → decodeAudioData); the values are for caches and humans.
OBJECT_KINDS: tuple[tuple[str, str, str], ...] = (
    ("a", "*.opus", "audio/ogg; codecs=opus"),
    ("a", "*.pk", "application/octet-stream"),
    ("c", "*.json", "application/json"),
    ("s", "*.json", "application/json"),
)


def kind_of(key: str) -> tuple[str, str, str] | None:
    """The OBJECT_KINDS row for bucket key `key`, or None when no publisher writes such a key
    (client files, songs.json, a stray .pk.tmp): --restamp leaves those alone."""
    top, _, rest = key.partition("/")
    for row in OBJECT_KINDS:
        if top == row[0] and rest and fnmatch.fnmatchcase(rest, row[1]):
            return row
    return None


def headers_for(key: str) -> tuple[str, str] | None:
    """(Content-Type, Cache-Control) the object at `key` must carry, or None (see kind_of)."""
    row = kind_of(key)
    return (row[2], IMMUTABLE) if row else None


def resolve_targets(paths: Paths, bucket: str | None, distribution: str | None) -> tuple[str, str | None]:
    bucket = bucket or os.environ.get("SFR_BUCKET")
    distribution = distribution or os.environ.get("SFR_DISTRIBUTION")
    if not bucket:
        # written by web/scripts/deploy.sh / terraform output -json > infra/live/outputs.json
        outputs = paths.out.parent / "infra" / "live" / "outputs.json"
        if outputs.exists():
            o = json.loads(outputs.read_text())
            bucket = o.get("bucket", {}).get("value")
            distribution = distribution or o.get("distribution_id", {}).get("value")
    if not bucket:
        raise SystemExit("publish: need --bucket (or SFR_BUCKET / infra/live/outputs.json)")
    return bucket, distribution


def commands(public: Path, bucket: str, distribution: str | None, *, dry_run: bool) -> list[list[str]]:
    dr = ["--dryrun"] if dry_run else []
    # --size-only for every kind: the names are content hashes, so a same-named object has the same bytes
    cmds = [["aws", "s3", "sync", str(public / pre) + "/", f"s3://{bucket}/{pre}/", "--exclude", "*", "--include", glob,
             "--content-type", ctype, "--cache-control", IMMUTABLE, "--size-only", *dr]
            for pre, glob, ctype in OBJECT_KINDS]
    cmds.append(["aws", "s3", "cp", str(public / "songs.json"), f"s3://{bucket}/songs.json",
                 "--content-type", "application/json", "--cache-control", SONGS_CC, *dr])
    if distribution and not dry_run:
        cmds.append(["aws", "cloudfront", "create-invalidation", "--distribution-id", distribution,
                     "--paths", "/songs.json"])
    return cmds


def publish(paths: Paths, bucket: str | None, distribution: str | None, *, dry_run: bool, echo=print) -> int:
    bucket, distribution = resolve_targets(paths, bucket, distribution)
    public = paths.public
    if not (public / "songs.json").exists():
        raise SystemExit("publish: out/public/songs.json missing — run `sfr manifest` first")
    for cmd in commands(public, bucket, distribution, dry_run=dry_run):
        echo("$ " + " ".join(cmd))
        r = subprocess.run(cmd)
        if r.returncode != 0:
            echo(f"publish: command failed rc={r.returncode}")
            return r.returncode
    if not dry_run:
        hist = paths.work / "publish"
        hist.mkdir(parents=True, exist_ok=True)
        listing = sorted(str(p.relative_to(public)) for p in public.rglob("*") if p.is_file())
        (hist / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.txt").write_text("\n".join(listing) + "\n")
    return 0


def prune(paths: Paths, bucket: str | None, *, dry_run: bool, echo=print) -> int:
    """Delete bucket objects under a/ c/ s/ that are referenced by neither the current
    out/public nor the previous publish listing. Manual only."""
    bucket, _ = resolve_targets(paths, bucket, None)
    public = paths.public
    current = {str(p.relative_to(public)) for p in public.rglob("*") if p.is_file()}
    # keep what is on disk now plus the last two *published* listings (the live songs.json may
    # still reference the previous publish's objects; CloudFront may still serve the one before)
    hist = sorted((paths.work / "publish").glob("*.txt")) if (paths.work / "publish").exists() else []
    previous: set[str] = set()
    for h in hist[-2:]:
        previous |= set(h.read_text().split())
    keep = current | previous
    r = subprocess.run(["aws", "s3api", "list-objects-v2", "--bucket", bucket, "--query", "Contents[].Key",
                        "--output", "json"], capture_output=True, text=True, check=True)
    keys = json.loads(r.stdout or "[]") or []
    doomed = [k for k in keys if k.split("/")[0] in ("a", "c", "s") and k not in keep]
    echo(f"prune: {len(keys)} objects in bucket, {len(keep)} referenced, {len(doomed)} to delete")
    if dry_run:
        for k in doomed[:50]:
            echo(f"  would delete {k}")
        if len(doomed) > 50:
            echo(f"  … and {len(doomed) - 50} more")
        return 0
    for i in range(0, len(doomed), 1000):
        batch = {"Objects": [{"Key": k} for k in doomed[i:i + 1000]], "Quiet": True}
        subprocess.run(["aws", "s3api", "delete-objects", "--bucket", bucket, "--delete", json.dumps(batch)],
                       check=True)
    return 0


# ---------------------------------------------------------------- restamp (#14 backfill)

def _aws_json(argv: list[str]):
    r = subprocess.run(["aws", *argv, "--output", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"aws {' '.join(argv[:3])} failed rc={r.returncode}: {r.stderr[-500:]}")
    return json.loads(r.stdout) if r.stdout.strip() else None


def _scopes(bucket: str) -> list[str]:
    """Prefixes --restamp works in: c/ plus a/<song>/ and s/<song>/ for every song with objects
    (from the delimiter listing, so a song already gone from songs.json still counts)."""
    scopes = {"c/"}
    for pre in ("a", "s"):
        scopes.update(_aws_json(["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", f"{pre}/",
                                 "--delimiter", "/", "--query", "CommonPrefixes[].Prefix"]) or [])
    return sorted(scopes)


def sample_keys(listing: Iterable[tuple[str, str]]) -> list[str]:
    """One key per (object kind, upload hour). Every object of a publish batch was stamped by the
    same command, so one HEAD per batch tells whether the whole batch is right — and a song with
    two batches hours apart (a host publish, then a shard re-render) gets both checked. One HEAD
    per object is not an option at ~10⁶ objects from a CLI subprocess each."""
    first: dict[tuple, str] = {}
    for key, modified in listing:
        first.setdefault((kind_of(key), modified[:13]), key)
    return list(first.values())


def stale(head: dict, key: str) -> str | None:
    """What the object carries today when that is not what headers_for() prescribes, else None."""
    want = headers_for(key)
    if want is None:
        return None
    got = (head.get("ContentType"), head.get("CacheControl"))
    return None if got == want else f"{got[0] or '-'} / {got[1] or '-'}"


def _inspect(bucket: str, scope: str) -> dict:
    """{"scope", "objects", "wrong": {glob: what the sampled object carries}} for one prefix."""
    listing = [tuple(x) for x in (_aws_json(["s3api", "list-objects-v2", "--bucket", bucket, "--prefix", scope,
                                             "--query", "Contents[].[Key,LastModified]"]) or [])]
    wrong: dict[str, str] = {}
    for key in sample_keys(listing):
        why = stale(_aws_json(["s3api", "head-object", "--bucket", bucket, "--key", key]) or {}, key)
        if why:
            wrong[kind_of(key)[1]] = why
    return {"scope": scope, "objects": len(listing), "wrong": wrong}


def restamp_commands(bucket: str, scope: str, globs: Iterable[str]) -> list[list[str]]:
    """Same-key copies with --metadata-directive REPLACE, the documented way to rewrite the
    headers of existing objects; one recursive `aws s3 cp` per kind, so the CLI does the copies
    server-side with its own concurrency. Restamping an object that was already right is
    harmless (no versioning, same bytes, same ETag)."""
    top = scope.split("/", 1)[0]
    src = f"s3://{bucket}/{scope}"
    return [["aws", "s3", "cp", src, src, "--recursive", "--exclude", "*", "--include", glob,
             "--metadata-directive", "REPLACE", "--content-type", ctype, "--cache-control", IMMUTABLE,
             "--only-show-errors"]
            for pre, glob, ctype in OBJECT_KINDS if pre == top and glob in globs]


def restamp(paths: Paths, bucket: str | None, distribution: str | None, *, dry_run: bool, echo=print,
            workers: int = 8) -> int:
    """Backfill Content-Type / Cache-Control on objects already in the bucket (#14: everything
    the cloud shards published before they stamped headers). Per prefix, samples one object per
    upload batch, restamps the kinds found wrong, then invalidates the touched top-level prefixes
    (a wildcard counts as one path). --dry-run only lists and HEADs."""
    bucket, distribution = resolve_targets(paths, bucket, distribution)
    scopes = _scopes(bucket)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        found = list(pool.map(lambda s: _inspect(bucket, s), scopes))
    bad = [f for f in found if f["wrong"]]
    objects = sum(f["objects"] for f in bad)
    echo(f"restamp: {len(scopes)} prefixes, {sum(f['objects'] for f in found)} objects; "
         f"{len(bad)} prefixes ({objects} objects, ≈ ${objects * 5e-6:.2f} in COPY requests) need restamping")
    for f in bad:
        got = "  ".join(f"{glob}: {why}" for glob, why in sorted(f["wrong"].items()))
        echo(f"  {f['scope']:48} {f['objects']:>7} objects  {got}")
    cmds = [c for f in bad for c in restamp_commands(bucket, f["scope"], f["wrong"])]
    touched = sorted({f["scope"].split("/", 1)[0] for f in bad})
    if distribution and touched:
        cmds.append(["aws", "cloudfront", "create-invalidation", "--distribution-id", distribution,
                     "--paths", *(f"/{t}/*" for t in touched)])
    for cmd in cmds:
        echo("$ " + " ".join(cmd))
        if dry_run:
            continue
        r = subprocess.run(cmd)
        if r.returncode != 0:
            echo(f"restamp: command failed rc={r.returncode}")
            return r.returncode
    if dry_run:
        echo("restamp: dry run — nothing changed")
    return 0
