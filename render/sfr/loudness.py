"""EBU R128 measurement and the master filter chain (plan §8.3 steps 2–4, D7).

Pure linear gain: gain_db = min(target − I, ceiling − TP), clamped to ±clamp. Never
loudnorm's dynamic second pass.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import RenderSettings
from .sched import JobError, run


def measure(wav: Path, settings: RenderSettings, timeout_s: int = 300) -> dict:
    """Return {'input_i': float, 'input_tp': float, 'input_lra': float, 'input_thresh': float}."""
    argv = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav),
            "-af", f"loudnorm=I={settings.lufs_target}:TP={settings.tp_ceiling_dbtp}:LRA=11:print_format=json",
            "-f", "null", "-"]
    res = run(argv, timeout_s=timeout_s, what="loudnorm measure")
    return parse_loudnorm(res.stderr.decode("utf-8", "replace"))


def parse_loudnorm(stderr: str) -> dict:
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, flags=re.S)
    if not m:
        raise JobError("measure", "loudnorm printed no JSON:\n" + stderr[-1500:])
    d = json.loads(m[-1])
    out = {}
    for k in ("input_i", "input_tp", "input_lra", "input_thresh"):
        v = d.get(k)
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            out[k] = float("-inf")
    return out


def gain_db(input_i: float, input_tp: float, settings: RenderSettings) -> float:
    g = min(settings.lufs_target - input_i, settings.tp_ceiling_dbtp - input_tp)
    c = settings.gain_clamp_db
    return max(-c, min(c, g))


def is_silent(input_i: float, settings: RenderSettings) -> bool:
    return not (input_i > settings.silent_below_lufs)   # handles -inf / nan


def master_filter(gain: float, settings: RenderSettings, duration_s: int, *,
                  start_offset_s: float = 0.0, drift_ppm: float = 0.0, native_rate: int = 48000) -> str:
    """ffmpeg -af chain: gain → (drift) → soxr resample → start alignment → exact length."""
    sr = settings.sample_rate
    parts = [f"volume={gain:.4f}dB", "aformat=channel_layouts=stereo"]
    if drift_ppm:
        # the engine's clock runs fast/slow by drift_ppm: relabel the rate before resampling.
        # asetrate takes integer Hz (22.7 ppm steps at 44.1 k), so go through a 10× intermediate
        # rate first: resolution becomes ~2.3 ppm at the cost of one extra (cheap) resample.
        hi = native_rate * 10
        parts.append(f"aresample={hi}:resampler=soxr:precision=28")
        parts.append(f"asetrate={round(hi * (1.0 + drift_ppm / 1e6))}")
    parts.append(f"aresample={sr}:resampler=soxr:precision=28")
    off = round(start_offset_s * sr)
    if off > 0:          # engine audio lags the reference → advance it
        parts.append(f"atrim=start_sample={off}")
    elif off < 0:        # engine audio leads → delay it
        parts.append(f"adelay={-off}S|{-off}S")
    parts.append("asetpts=PTS-STARTPTS")
    total = duration_s * sr
    parts.append(f"atrim=end_sample={total}")
    parts.append(f"apad=whole_len={total}")
    return ",".join(parts)


def write_master(raw_wav: Path, master_flac: Path, gain: float, settings: RenderSettings, duration_s: int, *,
                 start_offset_s: float = 0.0, drift_ppm: float = 0.0, native_rate: int = 48000,
                 timeout_s: int = 600) -> None:
    af = master_filter(gain, settings, duration_s, start_offset_s=start_offset_s, drift_ppm=drift_ppm,
                       native_rate=native_rate)
    tmp = master_flac.with_suffix(".flac.tmp")
    argv = ["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(raw_wav), "-af", af,
            "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24",
            "-f", "flac", str(tmp)]
    run(argv, timeout_s=timeout_s, what="master")
    tmp.replace(master_flac)
