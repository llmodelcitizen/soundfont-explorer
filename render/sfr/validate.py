"""Render validation (plan §8.4 `sfr manifest`): fast structural checks by default, full
decode with opusdec under --thorough. Failures become `excluded[]` entries, never blockers."""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Paths
from .jobs import Job, read_meta
from .loudness import PEAK_TOLERANCE_DB
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
    kept, master = bool(meta.get("master_kept", True)), job.master_path(paths).exists()
    if kept and not master:
        return Verdict(False, "no-master")
    # a master.flac this meta does not vouch for is the previous spec's audio that a re-render
    # without --keep-masters used to leave behind (#13). classify() already refuses it as a
    # re-encode source, so the encoded segments beside it are still the ones this meta
    # describes: report it, but do NOT fail the job. Excluding it would silently shrink a
    # published set for every variant a pre-fix image produced (the exclusion path drops
    # variants without refusing the song, unlike the never-rendered check).
    stale_master = master and not kept
    lufs = meta.get("lufs")
    if lufs is None or not (lufs > job.settings.silent_below_lufs):
        return Verdict(False, "silent")
    g = meta.get("gain_db")
    # the clamp is one-sided (#27): amplification is capped by policy, attenuation is whatever
    # peak safety demanded, so a large NEGATIVE gain is correct rather than out of range
    if g is None or g > job.settings.gain_clamp_db + 1e-6:
        return Verdict(False, "gain-out-of-range")
    # peak safety is a publication invariant, re-checked here against the recorded measurements
    # rather than trusted from the render: an unsafe artifact must never reach a set document.
    tp = meta.get("tp")
    if tp is None or not math.isfinite(tp) or not math.isfinite(g):
        return Verdict(False, "no-peak-measurement")
    if tp + g > job.settings.tp_ceiling_dbtp + PEAK_TOLERANCE_DB:
        return Verdict(False, "peak-unsafe")
    out_tp = meta.get("output_tp")            # absent in renders from before the artifact check
    if out_tp is not None and (not math.isfinite(out_tp)
                               or out_tp > job.settings.tp_ceiling_dbtp + PEAK_TOLERANCE_DB):
        return Verdict(False, "peak-unsafe")
    n = 0
    warnings: list[str] = ["stale-master: master.flac on disk that meta disowns; safe to delete"] if stale_master else []
    for p, want in job.segment_files(paths):
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


# ---------------------------------------------------------------- corpus audit (#27)

def peak_violations(paths, settings, *, current_pipeline: int) -> dict:
    """Walk work/renders and report anything that breaks the peak invariant or predates it.

    This is the instrument the acceptance criteria in #27 are stated in: "zero published
    peak-ceiling violations and zero stale artifacts from the prior pipeline version". It reads
    metas only — no decoding — so it is cheap enough to run over the whole corpus.
    """
    unsafe, stale, unreadable = [], [], []
    root = paths.renders
    for meta_path in sorted(root.glob("*/*/meta.json")) if root.exists() else []:
        song, variant = meta_path.parent.parent.name, meta_path.parent.name
        try:
            m = json.loads(meta_path.read_text())
        except (OSError, ValueError) as e:
            unreadable.append({"song": song, "variant": variant, "error": str(e)})
            continue
        if m.get("status") != "ok":
            continue
        if int(m.get("pipeline_version") or 0) < current_pipeline:
            stale.append({"song": song, "variant": variant,
                          "pipeline_version": m.get("pipeline_version")})
        tp, g, out_tp = m.get("tp"), m.get("gain_db"), m.get("output_tp")
        ceiling = settings.tp_ceiling_dbtp + PEAK_TOLERANCE_DB
        why = None
        if tp is None or g is None or not math.isfinite(tp) or not math.isfinite(g):
            why = "no usable peak measurement"
        elif tp + g > ceiling:
            why = f"tp+gain = {tp + g:.3f} dBTP"
        elif out_tp is not None and (not math.isfinite(out_tp) or out_tp > ceiling):
            why = f"output_tp = {out_tp} dBTP"
        if why:
            unsafe.append({"song": song, "variant": variant, "why": why,
                           "tp": tp, "gain_db": g, "output_tp": out_tp})
    return {"unsafe": unsafe, "stale": stale, "unreadable": unreadable,
            "ceiling_dbtp": settings.tp_ceiling_dbtp, "pipeline_version": current_pipeline}
