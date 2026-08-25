"""Shard planning + cost estimate for burst-fleet runs.

One implementation, two callers: render/cloud/submit.py (operator CLI on the workstation) and the
admin server (admin/server/sfadmin/renders.py). Pure functions — no AWS, no I/O beyond
reading the two songs.json documents, catalog/variants.json and render/engines.json.
"""
from __future__ import annotations

import json
import pathlib

# measured 2026-08-21 (docs/validation.md "M5"/"M7"): 116 CPU-h for 7935 jobs after
# PIPELINE_VERSION 2, i.e. ~53 CPU-s per job. Spot c7a is ~$0.019/vCPU-h.
CPU_S_PER_JOB = 53.0
USD_PER_VCPU_HOUR = 0.019
DEFAULT_DURATION_S = 180
# what sfr.jobs.variant_allowed_for_song admits when a song names no include_classes (and
# what canon.py writes into every song from corpus.json default_include_classes)
DEFAULT_INCLUDE_CLASSES = ("full_gm", "melodic_only", "partial")


def engine_availability(repo: pathlib.Path) -> tuple[dict[str, int], dict[str, str]]:
    """(renderable engine -> variants per song, engine in the catalog but not renderable
    on the fleet -> why). The first is the selection sfr's plan_jobs applies there:
    published, no ROM (roms/ is empty on the fleet), completeness within the default
    include_classes, engine pinned in render/engines.json. Naming an engine from the second
    set is still refused — nothing would render — but with the real reason (#19)."""
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
    """id -> duration_s from the build-side song tables (public + private when present)."""
    meta: dict[str, int] = {}
    for p in ("songs/songs.json", "songs/private/songs.json"):
        f = repo / p
        if f.exists():
            for s in json.loads(f.read_text())["songs"]:
                meta[s["id"]] = s["duration_s"]
    return meta


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


def estimate(shards: list[dict], variants: int, limit: int | None = None) -> tuple[float, float]:
    """(cpu_hours, usd) for the planned shards at `variants` jobs per song, under the run's
    per-shard `--limit`."""
    cpu_h = job_count(shards, variants, limit) * CPU_S_PER_JOB / 3600
    return cpu_h, cpu_h * USD_PER_VCPU_HOUR
