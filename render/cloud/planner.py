"""Shard planning + cost estimate for burst-fleet runs.

One implementation, two callers: render/cloud/submit.py (operator CLI on bimmer) and the
admin server (admin/server/sfadmin/renders.py). Pure functions — no AWS, no I/O beyond
reading the two songs.json documents.
"""
from __future__ import annotations

import json
import pathlib

# measured 2026-08-21 (docs/validation.md "M5"/"M7"): 116 CPU-h for 7935 jobs after
# PIPELINE_VERSION 2, i.e. ~53 CPU-s per job. Spot c7a is ~$0.019/vCPU-h.
CPU_S_PER_JOB = 53.0
USD_PER_VCPU_HOUR = 0.019
DEFAULT_DURATION_S = 180


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


def estimate(shards: list[dict], variants: int) -> tuple[float, float]:
    """(cpu_hours, usd) for the planned shards at `variants` jobs per song."""
    jobs = sum(len(s["songs"]) for s in shards) * variants
    cpu_h = jobs * CPU_S_PER_JOB / 3600
    return cpu_h, cpu_h * USD_PER_VCPU_HOUR
