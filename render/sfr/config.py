"""Paths, numbers and catalog loading for sfr.

All tunables come from render/engines.json ("render" section) so the web client
(web/src/config.ts) and the pipeline share one source of truth (plan §4).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RENDER_DIR = HERE.parent            # render/
REPO_ROOT = RENDER_DIR.parent       # repo root when running natively


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v) if v else default


@dataclass
class Paths:
    fonts: Path = field(default_factory=lambda: _env_path("SFR_FONTS", REPO_ROOT / "soundfonts"))
    songs: Path = field(default_factory=lambda: _env_path("SFR_SONGS", REPO_ROOT / "songs"))
    catalog: Path = field(default_factory=lambda: _env_path("SFR_CATALOG", REPO_ROOT / "catalog"))
    roms: Path = field(default_factory=lambda: _env_path("SFR_ROMS", REPO_ROOT / "roms"))
    work: Path = field(default_factory=lambda: _env_path("SFR_WORK", REPO_ROOT / "work"))
    out: Path = field(default_factory=lambda: _env_path("SFR_OUT", REPO_ROOT / "out"))
    engines_json: Path = field(default_factory=lambda: _env_path("SFR_ENGINES", RENDER_DIR / "engines.json"))
    banks: Path = field(default_factory=lambda: _env_path("SFR_BANKS", Path("/opt/banks")))

    @property
    def renders(self) -> Path:
        return self.work / "renders"

    @property
    def tmp(self) -> Path:
        return self.work / "tmp"

    @property
    def public(self) -> Path:
        return self.out / "public"

    @property
    def jobs_log(self) -> Path:
        return self.work / "jobs.log"

    @property
    def errors_log(self) -> Path:
        return self.work / "errors.log"

    def render_dir(self, song_id: str, variant_id: str) -> Path:
        return self.renders / song_id / variant_id


@dataclass(frozen=True)
class RenderSettings:
    sample_rate: int = 48000
    channels: int = 2
    slice_s: int = 2
    listen_slice_s: int = 10
    lead_in_s: float = 0.12
    lead_out_s: float = 0.02
    scrub_bitrate_kbps: int = 48
    listen_bitrate_kbps: int = 96
    opus_framesize_ms: int = 20
    opus_comp: int = 10
    pack_size: int = 24
    lufs_target: float = -16.0
    tp_ceiling_dbtp: float = -1.5
    gain_clamp_db: float = 30.0
    silent_below_lufs: float = -50.0
    tail_s: int = 3

    @property
    def lead_in_samples(self) -> int:
        return round(self.lead_in_s * self.sample_rate)

    @property
    def lead_out_samples(self) -> int:
        return round(self.lead_out_s * self.sample_rate)

    @property
    def segment_samples(self) -> int:
        return self.slice_s * self.sample_rate + self.lead_in_samples + self.lead_out_samples

    @property
    def listen_segment_samples(self) -> int:
        return self.listen_slice_s * self.sample_rate + self.lead_in_samples + self.lead_out_samples

    def n_slices(self, duration_s: int) -> int:
        return -(-duration_s // self.slice_s)

    def n_listen(self, duration_s: int) -> int:
        return -(-duration_s // self.listen_slice_s)

    @staticmethod
    def from_json(d: dict[str, Any]) -> "RenderSettings":
        fields = {k: d[k] for k in RenderSettings.__dataclass_fields__ if k in d}
        rs = RenderSettings(**fields)
        # sanity: engines.json carries the derived numbers too; they must agree
        if "segment_samples" in d:
            assert rs.segment_samples == d["segment_samples"], (rs.segment_samples, d["segment_samples"])
        if "listen_segment_samples" in d:
            assert rs.listen_segment_samples == d["listen_segment_samples"]
        return rs


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_engines(paths: Paths) -> dict[str, Any]:
    return load_json(paths.engines_json)


def load_settings(paths: Paths) -> RenderSettings:
    return RenderSettings.from_json(load_engines(paths)["render"])


def load_songs(paths: Paths) -> list[dict[str, Any]]:
    """songs/songs.json plus songs/private/songs.json (owner-supplied), if present."""
    songs: list[dict[str, Any]] = []
    for p in (paths.songs / "songs.json", paths.songs / "private" / "songs.json"):
        if p.exists():
            data = load_json(p)
            entries = data["songs"] if isinstance(data, dict) else data
            for s in entries:
                s = dict(s)
                s["_dir"] = p.parent
                songs.append(s)
    seen = set()
    for s in songs:
        if s["id"] in seen:
            raise ValueError(f"duplicate song id {s['id']}")
        seen.add(s["id"])
    return songs


def load_variants(paths: Paths) -> list[dict[str, Any]]:
    data = load_json(paths.catalog / "variants.json")
    variants = data["variants"] if isinstance(data, dict) else data
    return [v for v in variants if not v.get("alias_of")]


def song_midi_path(song: dict[str, Any]) -> Path:
    return Path(song["_dir"]) / f"{song['id']}.mid"
