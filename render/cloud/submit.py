"""Submit a render run to the burst fleet, watch it, and put the fleet back to sleep.

    python3 render/cloud/submit.py --all --shards 8 --dry-run     # always start here
    python3 render/cloud/submit.py --song freedoom-e1m1 --song bach-bwv565

The compute environment ships DISABLED and is disabled again on the way out (including on
Ctrl-C and on failure), so the fleet cannot be left running by walking away from this script.
The watchdog Lambda is the backstop for when it is not walked away from politely.
"""
from __future__ import annotations

import argparse, json, os, pathlib, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from planner import (estimate, job_count, plan_shards, song_durations,  # noqa: E402
                     variant_counts, variants_per_song)

REPO = pathlib.Path(__file__).resolve().parents[2]


def aws(*args: str) -> dict:
    r = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True, check=True)
    return json.loads(r.stdout) if r.stdout.strip() else {}


def outputs() -> dict:
    o = json.loads((REPO / "infra/live/outputs.json").read_text())
    rf = (o.get("render_fleet") or {}).get("value")
    if not rf:
        sys.exit("render fleet is not deployed: set enable_render_fleet=true, terraform apply, "
                 "then terraform -chdir=infra/live output -json > infra/live/outputs.json")
    return rf


def song_ids(args) -> list[str]:
    ids = []
    for p in ("songs/songs.json", "songs/private/songs.json"):
        f = REPO / p
        if f.exists():
            ids += [s["id"] for s in json.loads(f.read_text())["songs"]]
    if args.all:
        return ids
    # a mistyped --song used to vanish from the selection: the run went ahead with the rest
    # (or died with "no songs selected" when it was the only one) and nobody rendered the song
    unknown = sorted(set(args.song) - set(ids))
    if unknown:
        sys.exit(f"[submit] unknown song id(s): {', '.join(unknown)} (ids come from songs/songs.json)")
    return [s for s in ids if s in set(args.song)]


def set_state(ce: str, state: str) -> None:
    subprocess.run(["aws", "batch", "update-compute-environment",
                    "--compute-environment", ce, "--state", state], check=True, capture_output=True)
    print(f"[submit] compute environment {ce} -> {state}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--song", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--shards", type=int, default=8)
    ap.add_argument("--variants", type=int, default=None,
                    help="variants per song, for the estimate (default: counted from catalog/variants.json)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-usd", type=float, default=60.0,
                    help="refuse to submit above this estimate; raise deliberately")
    args = ap.parse_args()

    songs = song_ids(args)
    if not songs:
        sys.exit("no songs selected (use --all or --song ID)")
    shards = plan_shards(songs, args.shards, song_durations(REPO))
    variants = args.variants or variants_per_song(variant_counts(REPO))
    cpu_h, usd = estimate(shards, variants)

    print(f"[submit] {len(songs)} songs across {len(shards)} shards")
    for i, s in enumerate(shards):
        print(f"   shard {i}: {len(s['songs']):>2} songs, D={s['duration_total_s']:>5}s  "
              f"{' '.join(s['songs'])}")
    print(f"[submit] estimate: {job_count(shards, variants)} jobs ({variants}/song), {cpu_h:.0f} CPU-h, "
          f"~${usd:.2f} of spot (ceiling ${args.max_usd:.2f})")
    if usd > args.max_usd:
        sys.exit(f"[submit] REFUSING: estimate ${usd:.2f} exceeds --max-usd ${args.max_usd:.2f}")
    if args.dry_run:
        print("[submit] dry run, nothing submitted")
        return 0

    rf = outputs()
    (REPO / "work").mkdir(exist_ok=True)
    sf = REPO / "work" / "shards.json"
    sf.write_text(json.dumps(shards, indent=1))
    subprocess.run(["aws", "s3", "cp", str(sf), f"s3://{rf['fonts_bucket']}/shards.json"], check=True)
    # songs and catalog ride with the run rather than the image, so adding a song needs no rebuild
    # and owner-supplied songs/private/ never enters a registry. Both are small (MIDI + JSON).
    # songs/import/ is a staging area for MIDIs not yet in the corpus (4300 files, 134 MB) and
    # __pycache__ is build output; neither is an input to a render, and every shard would pull
    # both on every run.
    skip = ["--exclude", "import/*", "--exclude", "*__pycache__/*", "--exclude", "*.pyc"]
    for src, pre in ((REPO / "songs", "songs"), (REPO / "catalog", "catalog")):
        subprocess.run(["aws", "s3", "sync", str(src), f"s3://{rf['fonts_bucket']}/{pre}/",
                        "--delete", "--only-show-errors", *skip], check=True)
    print(f"[submit] staged shards.json, songs/ and catalog/ to s3://{rf['fonts_bucket']}/")

    set_state(rf["compute_environment"], "ENABLED")
    job_id = None
    try:
        sub = aws("batch", "submit-job", "--job-name", "soundfont-explorer-render",
                  "--job-queue", rf["job_queue"], "--job-definition", rf["job_definition"],
                  *(["--array-properties", f"size={len(shards)}"] if len(shards) > 1 else []))
        job_id = sub["jobId"]
        print(f"[submit] job {job_id}  (aws batch describe-jobs --jobs {job_id})")
        while True:
            j = aws("batch", "describe-jobs", "--jobs", job_id)["jobs"][0]
            st = j.get("arrayProperties", {}).get("statusSummary") or {j["status"]: 1}
            print(f"[submit] {time.strftime('%H:%M:%S')} {st}", flush=True)
            if j["status"] in ("SUCCEEDED", "FAILED"):
                print(f"[submit] terminal: {j['status']}")
                return 0 if j["status"] == "SUCCEEDED" else 1
            time.sleep(30)
    except KeyboardInterrupt:
        if job_id:
            subprocess.run(["aws", "batch", "terminate-job", "--job-id", job_id,
                            "--reason", "operator interrupt"], check=False)
            print(f"[submit] terminated {job_id}")
        return 130
    finally:
        # the whole point: the fleet is never left able to scale up unattended
        set_state(rf["compute_environment"], "DISABLED")


if __name__ == "__main__":
    sys.exit(main())
