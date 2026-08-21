"""Render validation (plan §8.4 `sfr manifest`): fast structural checks by default, full
decode with opusdec under --thorough. Failures become `excluded[]` entries, never blockers."""
from __future__ import annotations

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


# opusdec writes headerless s16le PCM to stdout when the output is "-" (a WAV header on a pipe
# would carry placeholder lengths), so the decoded sample count is simply bytes / 4 (stereo s16).
OPUSDEC = ["opusdec", "--quiet", "--rate", "48000", "--force-stereo"]


def _decoded_samples(path: Path, timeout: float = 120) -> int:
    """Fully decode one segment through a pipe and count the samples — nothing touches disk."""
    r = subprocess.run([*OPUSDEC, str(path), "-"], check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    if len(r.stdout) % 4:
        raise ValueError(f"odd PCM length {len(r.stdout)}")
    return len(r.stdout) // 4


def validate_job(job: Job, paths: Paths, *, thorough: bool = False) -> Verdict:
    """Read-only apart from spawning opusdec, so callers may run many jobs concurrently."""
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
            try:
                got = _decoded_samples(p)
            except subprocess.CalledProcessError as e:
                err = e.stderr.decode(errors="replace").strip().splitlines()
                return Verdict(False, f"decode:{p.name}:opusdec rc={e.returncode}" + (f" {err[-1]}" if err else ""), n)
            except Exception as e:  # noqa: BLE001
                return Verdict(False, f"decode:{p.name}:{e}", n)
            if got != want:
                return Verdict(False, f"decoded-samples:{p.name}:{got}!={want}", n)
        n += 1
    return Verdict(True, "", n, warnings)
