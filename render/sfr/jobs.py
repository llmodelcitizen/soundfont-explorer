"""Job model: (song, variant) → hashes, output layout, meta.json, planning and ordering.

Identity (plan §8.2):
  spec_hash   — everything that defines the job (song sha, variant, engine version, flags,
                numbers, pipeline version); used to decide "already done".
  master_hash — identity of master.flac (render inputs only).
  render_hash — identity of the encoded outputs = master_hash + encode params. This is the
                hash that appears in URLs (/a/<song>/l/<render_hash>/…) and in group hashes,
                so a bitrate change produces new immutable objects.
The filesystem is the database: work/renders/<song>/<variant>/meta.json.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import PIPELINE_VERSION
from .config import Paths, RenderSettings, song_midi_path

ENCODE_VERSION = 1   # bump when the slicing/encoding recipe changes


def _h(*parts: Any, n: int) -> str:
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:n]


@dataclass
class Job:
    song: dict[str, Any]
    variant: dict[str, Any]
    settings: RenderSettings
    engine_meta: dict[str, Any]
    # content digest of roms/<romset>/ (rom_digest()); only consulted for requires_rom variants
    rom_sha256: str | None = None

    # ---- identity -------------------------------------------------------
    @property
    def song_id(self) -> str:
        return self.song["id"]

    @property
    def variant_id(self) -> str:
        return self.variant["id"]

    @property
    def key(self) -> str:
        return f"{self.song_id}/{self.variant_id}"

    @property
    def engine(self) -> str:
        return self.variant["engine"]

    @property
    def core(self) -> str:
        return (self.variant.get("render") or {}).get("core") or self.variant.get("core") or "default"

    @property
    def duration_s(self) -> int:
        return int(self.song["duration_s"])

    @property
    def midi_path(self) -> Path:
        return song_midi_path(self.song)

    def _source_identity(self) -> dict[str, Any]:
        src = self.variant.get("source") or {}
        bank = self.variant.get("bank") or {}
        ident = {
            "sha256": src.get("sha256"),
            "bank": {k: bank.get(k) for k in ("kind", "number", "file", "sha256") if k in bank},
            "rom": self.variant.get("romset"),
        }
        if self.variant.get("requires_rom"):
            # the romset NAME is just a directory; the audio comes from the ROM images inside it
            # (MT-32 1.07 vs 2.04, SC-55mk2 1.00 vs 1.01 sound different). Added only for
            # requires_rom variants so every other master_hash stays exactly as it was.
            ident["rom_sha256"] = self.rom_sha256
        return ident

    def _engine_identity(self) -> dict[str, Any]:
        e = self.engine_meta
        return {
            "engine": self.engine,
            "version": e.get("version"),
            "commit": e.get("commit"),
            "base_args": e.get("base_args"),
            "chips": e.get("chips"),
            "core": self.core,
            "start_offset_s": e.get("start_offset_s") or 0.0,
            "drift_ppm": e.get("drift_ppm") or 0,
        }

    @property
    def master_hash(self) -> str:
        s = self.settings
        return _h(
            {"song_sha": self.song["sha256"], "variant": self.variant_id},
            self._source_identity(), self._engine_identity(),
            {"sr": s.sample_rate, "D": self.duration_s, "lufs": s.lufs_target, "tp": s.tp_ceiling_dbtp,
             "clamp": s.gain_clamp_db},
            {"pipeline": PIPELINE_VERSION}, n=12)

    @property
    def encode_params(self) -> dict[str, Any]:
        s = self.settings
        return {
            "lead_in": s.lead_in_samples, "lead_out": s.lead_out_samples,
            "slice_s": s.slice_s, "listen_slice_s": s.listen_slice_s,
            "scrub_kbps": s.scrub_bitrate_kbps, "listen_kbps": s.listen_bitrate_kbps,
            "framesize": s.opus_framesize_ms, "comp": s.opus_comp, "encode_version": ENCODE_VERSION,
        }

    @property
    def render_hash(self) -> str:
        return _h(self.master_hash, self.encode_params, n=12)

    @property
    def spec_hash(self) -> str:
        return _h(self.master_hash, self.render_hash, n=16)

    # ---- layout ---------------------------------------------------------
    def out_dir(self, paths: Paths) -> Path:
        return paths.render_dir(self.song_id, self.variant_id)

    def meta_path(self, paths: Paths) -> Path:
        return self.out_dir(paths) / "meta.json"

    def master_path(self, paths: Paths) -> Path:
        return self.out_dir(paths) / "master.flac"

    def seg_dir(self, paths: Paths) -> Path:
        return self.out_dir(paths) / "seg"

    def listen_dir(self, paths: Paths) -> Path:
        return self.out_dir(paths) / "listen"

    def tmp_dir(self, paths: Paths) -> Path:
        return paths.tmp / f"{self.song_id}__{self.variant_id}"

    @property
    def n_slices(self) -> int:
        return self.settings.n_slices(self.duration_s)

    @property
    def n_listen(self) -> int:
        return self.settings.n_listen(self.duration_s)

    def segment_files(self, paths: Paths) -> list[tuple[Path, int]]:
        """Every encoded segment with its exact sample count: the scrub tier seg/<i:04d>.opus,
        then the listen tier listen/<k:03d>.opus."""
        s = self.settings
        seg, lis = self.seg_dir(paths), self.listen_dir(paths)
        return ([(seg / f"{i:04d}.opus", s.segment_samples) for i in range(self.n_slices)]
                + [(lis / f"{k:03d}.opus", s.listen_segment_samples) for k in range(self.n_listen)])

    # ---- weight for ordering -------------------------------------------
    @property
    def source_bytes(self) -> int:
        return int((self.variant.get("source") or {}).get("bytes") or 0)


# ---- meta.json ----------------------------------------------------------

def read_meta(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_meta(path: Path, meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def clean_dir(p: Path) -> None:
    """Start from an empty directory."""
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)


def outputs_complete(job: Job, paths: Paths, *, need_master: bool = True) -> bool:
    if need_master and not job.master_path(paths).exists():
        return False
    return all(p.exists() for p, _ in job.segment_files(paths))


class State:
    DONE = "done"            # meta ok, spec matches, outputs present
    REENCODE = "reencode"    # master matches, encode params changed
    TODO = "todo"            # nothing usable
    FAILED = "failed"        # meta says failed (needs --retry / retry-failed)
    STALE = "stale"          # meta exists but spec differs → re-render


def classify(job: Job, paths: Paths) -> str:
    meta = read_meta(job.meta_path(paths))
    if meta is None:
        return State.TODO
    if meta.get("status") == "failed":
        return State.FAILED
    if meta.get("status") == "running":   # interrupted mid-job
        return State.TODO
    if meta.get("spec_hash") == job.spec_hash and outputs_complete(
            job, paths, need_master=bool(meta.get("master_kept", True))):
        return State.DONE
    if meta.get("master_hash") == job.master_hash and job.master_path(paths).exists():
        return State.REENCODE
    return State.STALE


# ---- planning -----------------------------------------------------------

def variant_allowed_for_song(variant: dict[str, Any], song: dict[str, Any]) -> bool:
    """Exclusion policy (plan §6): song.include_classes vs variant completeness."""
    classes = song.get("include_classes") or ["full_gm", "melodic_only", "partial"]
    facets = variant.get("facets") or {}
    comp = facets.get("completeness") or "full_gm"
    if comp in classes:
        return True
    if comp == "single_instrument":
        inst = (facets.get("instrument") or "").lower()
        return f"single_instrument:{inst}" in classes or "single_instrument" in classes
    return False


def plan_jobs(songs: Iterable[dict[str, Any]], variants: Iterable[dict[str, Any]],
              settings: RenderSettings, engines_json: dict[str, Any], *,
              song_ids: set[str] | None = None, variant_ids: set[str] | None = None,
              engines: set[str] | None = None, include_unpublished: bool = False,
              available_roms: Mapping[str, str] | set[str] | None = None) -> list[Job]:
    """available_roms: the romsets present under roms/ — None = do not filter. As a mapping
    (cli.available_roms(): romset -> rom_digest()) the digest also becomes part of every
    requires_rom job's master_hash; a plain set only filters."""
    jobs: list[Job] = []
    digests = available_roms if isinstance(available_roms, Mapping) else {}
    for song in songs:
        if song_ids and song["id"] not in song_ids:
            continue
        for v in variants:
            if variant_ids and v["id"] not in variant_ids:
                continue
            if engines and v["engine"] not in engines:
                continue
            if not include_unpublished and v.get("publish") is False:
                continue
            if v.get("requires_rom") and available_roms is not None and v.get("romset") not in available_roms:
                continue
            if not variant_allowed_for_song(v, song):
                continue
            emeta = engines_json["engines"].get(v["engine"])
            if not emeta or not emeta.get("version"):
                continue   # engine not installed/pinned yet (M2b)
            jobs.append(Job(song, v, settings, emeta, rom_sha256=digests.get(v.get("romset"))))
    return order_jobs(jobs)


def order_jobs(jobs: list[Job]) -> list[Job]:
    """Font-major, biggest fonts first (page cache stays hot, no stragglers), FM last."""
    return sorted(jobs, key=lambda j: (-j.source_bytes, j.variant_id, j.song_id))


def rom_digest(rom_dir: Path) -> str:
    """Identity of one roms/<romset>/ directory: sha256 over the sorted sha256s of the regular
    files directly inside it. File NAMES are deliberately left out — Nuked-SC55 and Munt both find
    their ROMs by content hash, so renaming a file changes nothing about the render — and the
    scan is non-recursive, matching what the engines look at."""
    sums = []
    for p in sorted(rom_dir.iterdir()):
        if p.is_file():
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    h.update(block)
            sums.append(h.hexdigest())
    return hashlib.sha256("\n".join(sorted(sums)).encode()).hexdigest()


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
