"""Render validation (plan §8.4 `sfr manifest`): fast structural checks by default, full
decode with opusdec under --thorough. Failures become `excluded[]` entries, never blockers."""
from __future__ import annotations

import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Paths
from .jobs import Job, read_meta
from .ogg import OggError, opus_info


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    checked: int = 0
    warnings: list[str] = field(default_factory=list)


def _decoded_samples(path: Path, tmp_wav: Path) -> int:
    subprocess.run(["opusdec", "--quiet", "--rate", "48000", "--force-stereo", str(path), str(tmp_wav)],
                   check=True, capture_output=True, timeout=120)
    data = tmp_wav.read_bytes()
    # RIFF parse: find 'data' chunk
    pos = 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        sz = struct.unpack_from("<I", data, pos + 4)[0]
        if cid == b"data":
            return sz // 4
        pos += 8 + sz + (sz & 1)
    raise ValueError("no data chunk")


def validate_job(job: Job, paths: Paths, *, thorough: bool = False, tmp_dir: Path | None = None) -> Verdict:
    meta = read_meta(job.meta_path(paths))
    if meta is None:
        return Verdict(False, "no-meta")
    if meta.get("status") != "ok":
        return Verdict(False, meta.get("reason") or meta.get("status") or "failed")
    if meta.get("spec_hash") != job.spec_hash:
        return Verdict(False, "stale-spec")
    if not job.master_path(paths).exists():
        return Verdict(False, "no-master")
    lufs = meta.get("lufs")
    if lufs is None or not (lufs > job.settings.silent_below_lufs):
        return Verdict(False, "silent")
    g = meta.get("gain_db")
    if g is None or abs(g) > job.settings.gain_clamp_db + 1e-6:
        return Verdict(False, "gain-out-of-range")
    s = job.settings
    checks = [(job.seg_dir(paths) / f"{i:04d}.opus", s.segment_samples) for i in range(job.n_slices)]
    checks += [(job.listen_dir(paths) / f"{k:03d}.opus", s.listen_segment_samples) for k in range(job.n_listen)]
    n = 0
    warnings: list[str] = []
    for p, want in checks:
        if not p.exists():
            return Verdict(False, f"missing:{p.parent.name}/{p.name}", n)
        try:
            info = opus_info(p)
        except OggError as e:
            return Verdict(False, f"bad-ogg:{p.name}:{e}", n)
        if info.samples != want:
            return Verdict(False, f"samples:{p.name}:{info.samples}!={want}", n)
        if info.channels != 2 or info.input_rate != 48000:
            warnings.append(f"{p.name}: channels={info.channels} rate={info.input_rate}")
        if thorough:
            td = tmp_dir or paths.tmp / "validate"
            td.mkdir(parents=True, exist_ok=True)
            tw = td / f"{job.song_id}__{job.variant_id}.wav"
            try:
                got = _decoded_samples(p, tw)
            except Exception as e:  # noqa: BLE001
                return Verdict(False, f"decode:{p.name}:{e}", n)
            finally:
                tw.unlink(missing_ok=True)
            if got != want:
                return Verdict(False, f"decoded-samples:{p.name}:{got}!={want}", n)
        n += 1
    return Verdict(True, "", n, warnings)
