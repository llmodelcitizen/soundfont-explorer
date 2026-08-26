"""Shard planning + cost estimate for burst-fleet runs.

One implementation, two callers: render/cloud/submit.py (operator CLI on the workstation) and the
admin server (admin/server/sfadmin/renders.py). Pure functions — no AWS, no I/O beyond
reading the two songs.json documents, catalog/variants.json and render/engines.json.
"""
from __future__ import annotations

import json
import pathlib

# Cost per job is NOT flat: it is a fixed process-overhead term plus one proportional to the
# song's duration. Measured on the two PIPELINE_VERSION 3 fleet runs of 2026-08-25, from
# wall time x allocated vCPU — what is actually billed, staging and publish tail included:
#
#     7,358 jobs, songs averaging 237 s of audio  ->  34.5 vCPU-s per job
#    20,376 jobs, songs averaging  90 s of audio  ->  18.8 vCPU-s per job
#
# No single constant fits both. The old CPU_S_PER_JOB = 53 (measured 2026-08-21 under
# PIPELINE_VERSION 2) overestimated them by 2.4x and 3.5x, which matters because `max_usd`
# refuses a run against this number — an accurate ceiling is the whole point of it.
#
# Solving the two simultaneously:
VCPU_S_PER_JOB_FIXED = 9.1     # engine + ffmpeg + ~150 short-lived opusenc, whatever the length
VCPU_S_PER_AUDIO_S = 0.107     # the actual DSP, per second of song

# Over-subscribing workers (SFR_WORKER_FACTOR) overlaps the FIXED term and leaves the DSP alone —
# that is what the knob is for, and modelling it as a gain on `a` rather than a flat discount is
# what makes it predict the right thing for long songs. Measured on the 48-song run of
# 2026-08-26 at 2x: 9.71 vCPU-s per job against 13.62 predicted at 1x, i.e. a/1.75 + b*d.
#
#      mean song      1x       2x    saving
#            42s   13.62     9.71       29%
#            90s   18.76    14.85       21%
#           237s   34.49    30.58       11%
#
# The saving shrinks as songs lengthen because a longer song is proportionally less overhead —
# which is exactly what was observed, and the reason not to quote one headline percentage.
#
# CAVEAT, and a different one from the a/b caveat below: this is ONE data point fitting ONE
# unknown, and the *structure* (gain applies to overhead only) is a physical argument, not a
# measurement. It is deliberately NOT extrapolated past the measured 2x — beyond that the gain
# is held flat, because nothing has tested 3x and guessing upward would under-price a run.
OVERHEAD_GAIN_AT_2X = 1.75


def overhead_gain(worker_factor: float | None) -> float:
    """How much of the fixed per-job overhead over-subscription overlaps away."""
    f = max(1.0, float(worker_factor or 1.0))
    return 1.0 + (OVERHEAD_GAIN_AT_2X - 1.0) * min(f - 1.0, 1.0)
#
# The fixed term is 49% of a 90 s song's job cost and 26% of a 237 s song's. That is not a
# curve-fitting artefact — the same runs showed every worker busy with CPU at 37-60%, EBS at 2%
# of provision and memory admission at 7% used, which is what per-job process overhead looks
# like from the outside (#45's corrected diagnosis).
#
# CAVEAT: two runs, two unknowns, so these FIT the data rather than being validated by it. The
# structure is physically justified but a third run at a different mean duration is the real
# test. Erring high is the safer direction for a spend ceiling.
#
# $/vCPU-h re-checked against live Spot 2026-08-25: c7a.16xlarge $0.0198, c7i.16xlarge $0.0161,
# c7a.8xlarge $0.0205.
USD_PER_VCPU_HOUR = 0.019
DEFAULT_DURATION_S = 180
# what sfr.jobs.variant_allowed_for_song admits when a song names no include_classes (and
# what canon.py writes into every song from canon.DEFAULT_INCLUDE_CLASSES)
DEFAULT_INCLUDE_CLASSES = ("full_gm", "melodic_only", "partial")


def engine_availability(repo: pathlib.Path) -> tuple[dict[str, int], dict[str, str]]:
    """(renderable engine -> variants per song, engine in the catalog but not renderable
    on the fleet -> why). The first is the selection sfr's plan_jobs applies there:
    published, no ROM (roms/ is empty on the fleet), completeness within the default
    include_classes, engine pinned in render/engines.json. Naming an engine from the second
    set is still refused — nothing would render — but with the real reason (#19).

    plan_jobs itself does not test alias_of; the filter below mirrors sfr.config.load_variants,
    which drops aliases before cli.py hands the list to plan_jobs. A byte-identical twin is
    rendered once, under the canonical id, so counting it would over-estimate."""
    cat = json.loads((repo / "catalog" / "variants.json").read_text())
    engines = json.loads((repo / "render" / "engines.json").read_text())["engines"]
    counts: dict[str, int] = {}
    why: dict[str, str] = {}
    for v in cat["variants"] if isinstance(cat, dict) else cat:
        if v.get("alias_of") or v.get("publish") is False:
            continue
        if ((v.get("facets") or {}).get("completeness") or "full_gm") not in DEFAULT_INCLUDE_CLASSES:
            continue
        if v.get("requires_rom"):
            why.setdefault(v["engine"], "needs a ROM, and the fleet's roms/ is empty")
            continue
        if not (engines.get(v["engine"]) or {}).get("version"):
            why.setdefault(v["engine"], "no pinned version in render/engines.json")
            continue  # engine not installed/pinned yet (M2b)
        counts[v["engine"]] = counts.get(v["engine"], 0) + 1
    return counts, {e: r for e, r in why.items() if e not in counts}


def variant_counts(repo: pathlib.Path) -> dict[str, int]:
    """engine -> variants one song renders by default. This is the "566" that used to be a
    literal in the admin and submit.py — it was this count on 2026-08-21 and nothing tied
    it to the catalog (#19). Songs with extra_include_classes plan a handful more; the
    estimate is a cost guard, not a ledger."""
    return engine_availability(repo)[0]


def variants_per_song(counts: dict[str, int], engines: list[str] | None = None,
                      limit: int | None = None,
                      unavailable: dict[str, str] | None = None) -> int:
    """Jobs one song plans under the run's knobs: an engine subset narrows the matrix and
    --limit caps it (see job_count — sfr applies --limit per shard, so this is only a
    per-song ceiling). The estimate used to ignore both, so max_usd refused affordable
    engine-subset runs (#19). An engine that would plan nothing is an error: `unavailable`
    tells a real-but-unrenderable engine (a ROM engine) from a typo."""
    if engines:
        unknown = sorted(set(engines) - set(counts))
        blocked = [e for e in unknown if e in (unavailable or {})]
        if blocked:
            raise ValueError("; ".join(f"engine {e!r} cannot run on the fleet: {unavailable[e]}"
                                       for e in blocked))
        if unknown:
            raise ValueError(f"unknown engines {unknown} (known: {sorted(counts)})")
        n = sum(counts[e] for e in set(engines))
    else:
        n = sum(counts.values())
    return min(n, limit) if limit else n


def job_count(shards: list[dict], variants: int, limit: int | None = None) -> int:
    """Jobs the fleet will actually run. Each shard runs ONE `sfr render --limit N` over all
    of its songs and sfr truncates the shard's flat job list once (render/sfr/cli.py), so
    --limit is a per-shard cap. Counting it per song over-estimated a limited run by the
    songs-per-shard ratio — 60 songs / 4 shards / --limit 5 read as 300 jobs, not 20 (#19)."""
    return sum(min(len(s["songs"]) * variants, limit) if limit else len(s["songs"]) * variants
               for s in shards)


def song_durations(repo: pathlib.Path) -> dict[str, int]:
    """id -> duration_s from the build-side songs.json."""
    f = repo / "songs/songs.json"
    if not f.exists():
        return {}
    return {s["id"]: s["duration_s"] for s in json.loads(f.read_text())["songs"]}


def plan_shards(songs: list[str], n: int, durations: dict[str, int]) -> list[dict]:
    """Longest-first greedy over shards. Song cost tracks duration_s, and the tail of a run
    is bounded by the slowest shard, so the long songs are dealt first."""
    order = sorted(songs, key=lambda s: -durations.get(s, DEFAULT_DURATION_S))
    shards: list[dict] = [{"songs": [], "d": 0} for _ in range(min(n, len(order)))]
    for s in order:
        t = min(shards, key=lambda x: x["d"])
        t["songs"].append(s)
        t["d"] += durations.get(s, DEFAULT_DURATION_S)
    return [{"songs": x["songs"], "duration_total_s": x["d"]} for x in shards if x["songs"]]


def job_cost_vcpu_s(mean_duration_s: float, worker_factor: float | None = None) -> float:
    """vCPU-seconds one job costs for a song of this length — overhead plus DSP."""
    return (VCPU_S_PER_JOB_FIXED / overhead_gain(worker_factor)
            + VCPU_S_PER_AUDIO_S * mean_duration_s)


def estimate(shards: list[dict], variants: int, limit: int | None = None,
             worker_factor: float | None = None) -> tuple[float, float]:
    """(cpu_hours, usd) for the planned shards at `variants` jobs per song, under the run's
    per-shard `--limit`.

    Priced per shard rather than per fleet, because cost tracks the length of the songs a shard
    actually holds: plan_shards() is longest-first greedy, so a shard of long songs costs more
    per job than one of short songs and a fleet-wide average would misprice both.

    `--limit` truncates the shard's flat job list ONCE, and that list is font-major across all
    of the shard's songs, so a limited run samples them roughly evenly — the shard's mean
    duration stays the right price for a truncated job too.
    """
    vcpu_s = 0.0
    for shard in shards:
        n = len(shard["songs"])
        if not n:
            continue
        jobs = min(n * variants, limit) if limit else n * variants
        total_s = shard.get("duration_total_s") or n * DEFAULT_DURATION_S
        vcpu_s += jobs * job_cost_vcpu_s(total_s / n, worker_factor)
    cpu_h = vcpu_s / 3600
    return cpu_h, cpu_h * USD_PER_VCPU_HOUR
