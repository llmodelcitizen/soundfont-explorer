"""Upload out/public to S3 and invalidate songs.json (plan §8.4). Every call is a real,
billable action: always run with --dry-run first."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from .config import Paths

IMMUTABLE = "public,max-age=31536000,immutable"
SONGS_CC = "public,max-age=60,stale-while-revalidate=600"


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
    a = f"s3://{bucket}/a/"
    cmds = [
        ["aws", "s3", "sync", str(public / "a") + "/", a, "--exclude", "*", "--include", "*.opus",
         "--content-type", "audio/ogg; codecs=opus", "--cache-control", IMMUTABLE, "--size-only", *dr],
        ["aws", "s3", "sync", str(public / "a") + "/", a, "--exclude", "*", "--include", "*.pk",
         "--content-type", "application/octet-stream", "--cache-control", IMMUTABLE, "--size-only", *dr],
        ["aws", "s3", "sync", str(public / "c") + "/", f"s3://{bucket}/c/", "--exclude", "*", "--include", "*.json",
         "--content-type", "application/json", "--cache-control", IMMUTABLE, *dr],
        ["aws", "s3", "sync", str(public / "s") + "/", f"s3://{bucket}/s/", "--exclude", "*", "--include", "*.json",
         "--content-type", "application/json", "--cache-control", IMMUTABLE, *dr],
        ["aws", "s3", "cp", str(public / "songs.json"), f"s3://{bucket}/songs.json",
         "--content-type", "application/json", "--cache-control", SONGS_CC, *dr],
    ]
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
