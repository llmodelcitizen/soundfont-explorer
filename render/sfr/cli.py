"""Command-line entry point (plan §8.2):

  sfr doctor | plan | render | retry-failed | pack | manifest | publish | status | calibrate
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

from . import __version__
from .config import Paths, load_engines, load_settings, load_songs, load_variants
from .jobs import State, classify, plan_jobs, read_meta


def add_path_args(p: argparse.ArgumentParser, top: bool = False) -> None:
    """Path flags are accepted both before and after the sub-command (SUPPRESS keeps the
    sub-parser from clobbering values given at the top level)."""
    g = p.add_argument_group("paths (defaults from SFR_* env; the image sets them to the bind mounts)")
    d = None if top else argparse.SUPPRESS
    for name in ("fonts", "songs", "catalog", "roms", "work", "out"):
        g.add_argument(f"--{name}", type=Path, default=d)
    g.add_argument("--engines", type=Path, dest="engines_json", default=d, help="render/engines.json")


def add_select_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("job selection")
    g.add_argument("--song", action="append", help="song id (repeatable)")
    g.add_argument("--variant", action="append", help="variant id (repeatable)")
    g.add_argument("--engine", action="append", help="engine id (repeatable)")
    g.add_argument("--all", action="store_true", help="all songs × all variants")
    g.add_argument("--limit", type=int, help="first N jobs (after ordering)")
    g.add_argument("--include-unpublished", action="store_true")


def paths_from(args) -> Paths:
    kw = {k: getattr(args, k) for k in ("fonts", "songs", "catalog", "roms", "work", "out") if getattr(args, k, None)}
    if getattr(args, "engines_json", None):
        kw["engines_json"] = args.engines_json
    return Paths(**kw)


def available_roms(paths: Paths) -> set[str]:
    if not paths.roms.exists():
        return set()
    return {p.name for p in paths.roms.iterdir() if p.is_dir()}


def select_jobs(args, paths: Paths):
    settings = load_settings(paths)
    engines_json = load_engines(paths)
    songs = load_songs(paths)
    variants = load_variants(paths)
    if not args.all and not args.song and not args.variant and not args.engine:
        raise SystemExit("select jobs with --song/--variant/--engine or --all")
    jobs = plan_jobs(songs, variants, settings, engines_json,
                     song_ids=set(args.song) if args.song else None,
                     variant_ids=set(args.variant) if args.variant else None,
                     engines=set(args.engine) if args.engine else None,
                     include_unpublished=args.include_unpublished,
                     available_roms=available_roms(paths))
    if args.limit:
        jobs = jobs[: args.limit]
    return jobs, settings, engines_json, songs, variants


def validate_environment(paths: Paths, jobs) -> None:
    """Startup checks (plan §8.2): tools present, --emu-X flags known, free disk ≥ 100 GB."""
    engines_needed = {j.engine for j in jobs}
    tools = {"ffmpeg", "opusenc"}
    ej_engines = load_engines(paths)["engines"]
    for e in engines_needed:
        b = (ej_engines.get(e) or {}).get("binary")
        if b:
            tools.add(b)
    missing = [t for t in sorted(tools) if shutil.which(t) is None]
    if missing:
        raise SystemExit(f"missing tools: {', '.join(missing)} (run inside the sfr-render image)")
    if "adlmidi" in engines_needed:
        import subprocess
        from .engines.adl import core_flag
        usage = subprocess.run(["adlmidiplay", "--help"], capture_output=True, text=True).stdout
        for j in jobs:
            if j.engine == "adlmidi":
                flag = core_flag(j.core)
                if flag not in usage:
                    raise SystemExit(f"adlmidiplay does not know {flag} (an unknown flag is silently "
                                     f"treated as a bank file — MVP footgun)")
    # soft version check: a package upgrade would silently change audio behind an unchanged hash
    try:
        import subprocess
        ej = load_engines(paths)["engines"]
        if "fluidsynth" in engines_needed and ej.get("fluidsynth", {}).get("version"):
            out = subprocess.run(["fluidsynth", "--version"], capture_output=True, text=True).stdout
            want = ej["fluidsynth"]["version"]
            if want not in out:
                print(f"WARNING: fluidsynth reports {out.strip().splitlines()[0]!r} but engines.json pins {want}; "
                      f"update engines.json (changes master hashes) or rebuild the image", file=sys.stderr)
    except Exception:  # noqa: BLE001 - advisory only
        pass
    paths.work.mkdir(parents=True, exist_ok=True)
    st = os.statvfs(paths.work)
    free_gb = st.f_bavail * st.f_frsize / 1e9
    if free_gb < 100:
        raise SystemExit(f"only {free_gb:.0f} GB free under {paths.work}; need ≥ 100 GB")


# ---------------------------------------------------------------- commands

def cmd_doctor(args) -> int:
    import subprocess
    tools = {
        "adlmidiplay": ["adlmidiplay", "--help"], "fluidsynth": ["fluidsynth", "--version"],
        "opnmidiplay": ["opnmidiplay", "--help"], "edmidi-render": ["edmidi-render", "--help"],
        "timidity": ["timidity", "--version"], "nuked-sc55-render": ["nuked-sc55-render", "--help"],
        "mt32emu-smf2wav": ["mt32emu-smf2wav", "--help"],
        "ffmpeg": ["ffmpeg", "-hide_banner", "-version"], "ffprobe": ["ffprobe", "-hide_banner", "-version"],
        "opusenc": ["opusenc", "--version"], "opusdec": ["opusdec", "--version"],
    }
    for name, argv in tools.items():
        path = shutil.which(argv[0])
        if not path:
            print(f"{name:18} -")
            continue
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            lines = [ln for ln in (out.stdout or out.stderr).splitlines() if ln.strip()]
            print(f"{name:18} {path}  {lines[0][:70] if lines else ''}")
        except Exception as e:  # pragma: no cover
            print(f"{name:18} {path}  (error: {e})")
    paths = paths_from(args)
    for name in ("fonts", "songs", "catalog", "roms", "work", "out"):
        p = getattr(paths, name)
        print(f"{name:18} {p}  {'ok' if p.exists() else 'MISSING'}")
    print(f"{'engines.json':18} {paths.engines_json}  {'ok' if paths.engines_json.exists() else 'MISSING'}")
    return 0


def cmd_plan(args) -> int:
    paths = paths_from(args)
    jobs, *_ = select_jobs(args, paths)
    states = Counter()
    by_engine = Counter()
    for j in jobs:
        states[classify(j, paths)] += 1
        by_engine[j.engine] += 1
    print(f"{len(jobs)} jobs; by engine: {dict(by_engine)}; states: {dict(states)}")
    if args.verbose:
        for j in jobs:
            print(f"  {classify(j, paths):9} {j.key}  w={max(1, -(-j.source_bytes // (256 << 20)))}")
    return 0


def cmd_render(args, retry_failed: bool = False) -> int:
    from .render import run_job
    from .sched import Logs, Runner
    paths = paths_from(args)
    jobs, settings, engines_json, *_ = select_jobs(args, paths)
    if retry_failed:
        jobs = [j for j in jobs if classify(j, paths) == State.FAILED]
    else:
        jobs = [j for j in jobs if classify(j, paths) != State.DONE]
    if not jobs:
        print("nothing to do")
        return 0
    validate_environment(paths, jobs)
    print(f"[sfr] {len(jobs)} jobs, {args.workers} workers, {args.mem_units} memory units")
    logs = Logs(paths.jobs_log, paths.errors_log)
    runner = Runner(args.workers, args.mem_units, logs)
    outcomes = runner.run_all(jobs, lambda j: run_job(j, paths, sem=runner.sem, retry=retry_failed or args.retry,
                                                      keep_tmp=args.keep_tmp, engines_json=engines_json, logs=logs))
    c = Counter(o.status for o in outcomes)
    reasons = Counter(o.reason.split(":")[0] for o in outcomes if o.status == "failed")
    print(f"[sfr] finished: {dict(c)}" + (f" failure reasons: {dict(reasons)}" if reasons else ""))
    return 0 if not c.get("failed") else 1


def cmd_status(args) -> int:
    paths = paths_from(args)
    rows = []
    if paths.renders.exists():
        for song_dir in sorted(paths.renders.iterdir()):
            for vdir in sorted(song_dir.iterdir()):
                m = read_meta(vdir / "meta.json")
                if m:
                    rows.append(m)
    per_song: dict[str, Counter] = defaultdict(Counter)
    for m in rows:
        per_song[m["song"]][m.get("status", "?")] += 1
    if args.json:
        out = {"renders": len(rows), "songs": {s: dict(c) for s, c in per_song.items()},
               "failed": [{"song": m["song"], "variant": m["variant"], "reason": m.get("reason")}
                          for m in rows if m.get("status") == "failed"]}
        print(json.dumps(out, indent=1))
        return 0
    print(f"{len(rows)} renders under {paths.renders}")
    for s, c in sorted(per_song.items()):
        print(f"  {s:24} {dict(c)}")
    fails = Counter(m.get("reason", "?").split(":")[0] for m in rows if m.get("status") == "failed")
    if fails:
        print(f"  failure reasons: {dict(fails)}")
    return 0


def cmd_manifest(args) -> int:
    """Validate + pack + write manifests. Only --song narrows the work; every other song keeps its
    current published set, and --variant/--engine/--limit are ignored (a set must always describe
    every rendered variant of a song, never a subset)."""
    from .manifest import build_manifests
    paths = paths_from(args)
    if args.variant or args.engine or args.limit:
        print("manifest: --variant/--engine/--limit are ignored (sets always cover every variant)", file=sys.stderr)
    args.variant = args.engine = None
    args.limit = None
    if not args.song:
        args.all = True
    jobs, settings, engines_json, songs, variants = select_jobs(args, paths)
    by_song: dict[str, list] = defaultdict(list)
    for j in jobs:
        by_song[j.song_id].append(j)
    if args.song:
        for sid in args.song:
            by_song.setdefault(sid, [])   # selected but nothing planned → still rebuilt (possibly empty)
    report = build_manifests(paths, songs, variants, settings, engines_json, by_song, thorough=args.thorough,
                             defaults={"song": args.default_song, "variant": args.default_variant})
    print(json.dumps(report, indent=1))
    return 0


def cmd_pack(args) -> int:
    # pack is part of manifest (idempotent); kept as an alias that skips writing songs.json? No —
    # keep one code path so order/groups can never diverge from the manifest.
    return cmd_manifest(args)


def cmd_publish(args) -> int:
    from .publish import prune, publish
    paths = paths_from(args)
    if args.prune:
        return prune(paths, args.bucket, dry_run=args.dry_run)
    return publish(paths, args.bucket, args.distribution, dry_run=args.dry_run)


def cmd_calibrate(args) -> int:
    from .calibrate import apply_to_engines_json, calibrate
    paths = paths_from(args)
    settings = load_settings(paths)
    engines_json = load_engines(paths)
    variants = {v["id"]: v for v in load_variants(paths)}
    reps: dict[str, dict] = {}
    for spec in args.rep:
        eid, _, vid = spec.partition("=")
        if vid not in variants:
            raise SystemExit(f"unknown variant {vid}")
        reps[eid] = variants[vid]
    if "fluidsynth" not in reps:
        raise SystemExit("need a FluidSynth representative: --rep fluidsynth=sf2-…")
    results = calibrate(paths, settings, engines_json, reps)
    print(json.dumps(results, indent=1))
    if args.write:
        apply_to_engines_json(paths.engines_json, results)
        print(f"updated {paths.engines_json}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sfr", description="Soundfont Explorer render pipeline")
    p.add_argument("--version", action="version", version=f"sfr {__version__}")
    add_path_args(p, top=True)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("doctor", help="tool versions and paths in this environment"); add_path_args(s)
    s = sub.add_parser("plan", help="list jobs and their state"); add_path_args(s); add_select_args(s)
    s.add_argument("-v", "--verbose", action="store_true")
    for name, help_ in (("render", "render + encode selected jobs"), ("retry-failed", "re-run failed jobs")):
        s = sub.add_parser(name, help=help_); add_path_args(s); add_select_args(s)
        s.add_argument("--workers", type=int, default=32)
        s.add_argument("--mem-units", type=int, default=64, help="256 MB admission units")
        s.add_argument("--retry", action="store_true", help="also re-run previously failed jobs")
        s.add_argument("--keep-tmp", action="store_true")
    for name in ("manifest", "pack"):
        s = sub.add_parser(name, help="validate, pack and write /c /s /songs.json under out/public")
        add_path_args(s); add_select_args(s)
        s.add_argument("--thorough", action="store_true", help="decode every segment with opusdec")
        s.add_argument("--default-song"); s.add_argument("--default-variant", default="adl-b58")
    s = sub.add_parser("publish", help="aws s3 sync out/public (dry-run first!)"); add_path_args(s)
    s.add_argument("--bucket"); s.add_argument("--distribution")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--prune", action="store_true")
    s = sub.add_parser("status", help="summarize work/renders"); add_path_args(s)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("worker", help="phase-2 upload worker (stub)"); add_path_args(s)
    s.add_argument("--queue", help="SQS queue URL")
    s = sub.add_parser("calibrate", help="measure per-engine start offset / drift with a click track")
    add_path_args(s)
    s.add_argument("--rep", action="append", default=[], help="engine=variant-id (repeatable)")
    s.add_argument("--write", action="store_true", help="write results into engines.json")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    try:
        if args.cmd == "doctor":
            return cmd_doctor(args)
        if args.cmd == "plan":
            return cmd_plan(args)
        if args.cmd == "render":
            return cmd_render(args)
        if args.cmd == "retry-failed":
            return cmd_render(args, retry_failed=True)
        if args.cmd in ("manifest", "pack"):
            return cmd_manifest(args)
        if args.cmd == "publish":
            return cmd_publish(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "calibrate":
            return cmd_calibrate(args)
        if args.cmd == "worker":
            from .worker import run as worker_run
            return worker_run(args.queue)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    print(f"unknown command {args.cmd}", file=sys.stderr)
    return 2
