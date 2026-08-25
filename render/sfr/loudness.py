"""EBU R128 measurement and the master filter chain (plan §8.3 steps 2–4, D7).

Pure linear gain: gain_db = min(target − I, ceiling − TP), clamped to ±clamp. Never
loudnorm's dynamic second pass.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .config import RenderSettings
from .sched import JobError, run


def measure(wav: Path, settings: RenderSettings, timeout_s: int = 300, in_args: list[str] | None = None) -> dict:
    """Return {'input_i': float, 'input_tp': float, 'input_lra': float, 'input_thresh': float}.

    Uses `ebur128`, not `loudnorm`: we only ever want the measured I/TP for a linear gain (D7),
    and loudnorm additionally builds its dynamic-normalisation state, which we throw away.
    Measured 5.85x faster over 8 variants spanning every engine, with gain_db agreeing to
    <= 0.03 dB (docs/validation.md "M7"). Both report `silent` identically: on pure silence
    loudnorm gives -inf and ebur128 floors at -70 LUFS, and the threshold is -50.
    """
    argv = ["ffmpeg", "-hide_banner", "-nostats", *(in_args or []), "-i", str(wav),
            "-af", "ebur128=peak=true", "-f", "null", "-"]
    res = run(argv, timeout_s=timeout_s, what="ebur128 measure")
    return parse_ebur128(res.stderr.decode("utf-8", "replace"))


def _f(text: str, pattern: str, what: str) -> float:
    """Parse one number out of the ebur128 summary; `-inf`/`inf` are real values here.

    A line that is missing altogether is a parse failure, not a value: turning it into -inf
    would make a missing `Peak:` line disable the true-peak ceiling (gain_db takes
    min(target - I, ceiling - (-inf)) = the loudness term alone) and a missing `I:` line fail
    the job as "silent" for the wrong reason. Either way the summary format has changed under
    us and the render must stop rather than master with a bogus gain.
    """
    m = re.search(pattern, text)
    if not m:
        raise JobError("measure", f"ebur128 summary has no {what} line:\n" + text[-1500:])
    try:
        return float(m.group(1))
    except ValueError:
        raise JobError("measure", f"ebur128 {what} is not a number: {m.group(1)!r}") from None


def parse_ebur128(stderr: str) -> dict:
    """Parse the trailing `Summary:` block of the ebur128 filter."""
    i = stderr.rfind("Summary:")
    if i < 0:
        raise JobError("measure", "ebur128 printed no summary:\n" + stderr[-1500:])
    s = stderr[i:]
    num = r"(-?(?:inf|[\d.]+))"
    return {
        "input_i": _f(s, r"I:\s*" + num + r"\s*LUFS", "integrated loudness (I:)"),
        "input_tp": _f(s, r"Peak:\s*" + num + r"\s*dBFS", "true peak (Peak:)"),
        "input_lra": _f(s, r"LRA:\s*" + num + r"\s*LU", "loudness range (LRA:)"),
        "input_thresh": _f(s, r"Threshold:\s*" + num + r"\s*LUFS", "threshold (Threshold:)"),
    }


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
            # -inf here is not a measurement, it is a parse failure wearing one: as a true peak
            # it disables the ceiling entirely (min(target - I, ceiling + inf)) and masters hot.
            # parse_ebur128's _f() has always raised on this; so does this path now (#27).
            raise JobError("measure", f"loudnorm {k} is not a number: {v!r}") from None
    return out


# The mastered signal must sit under the ceiling; the tolerance covers the %.4f rounding of the
# `volume=` filter argument and float noise, and nothing else.
PEAK_TOLERANCE_DB = 0.01


def gain_db(input_i: float, input_tp: float, settings: RenderSettings) -> float:
    """Linear gain for this render: the quieter of the loudness target and the peak ceiling.

    The clamp is ASYMMETRIC on purpose (#27). `+gain_clamp_db` is a policy limit — do not
    amplify a quiet render into noise. There is no matching policy for attenuation: cutting is
    what keeps the master under the true-peak ceiling, and the old symmetric
    `max(-clamp, ...)` silently handed back gain a hot renderer needed removed, so the master
    clipped and nothing downstream checked. Required attenuation is applied in full.
    """
    if not math.isfinite(input_tp):
        # -inf/nan reaches `min()` as a winner and yields a nonsense gain (+inf clamps to +clamp).
        # A measurement we cannot trust must stop the render, never master on a guess.
        raise JobError("master", f"true peak is not a finite number: {input_tp!r}")
    if not math.isfinite(input_i):
        raise JobError("master", f"integrated loudness is not a finite number: {input_i!r}")
    g = min(settings.lufs_target - input_i, settings.tp_ceiling_dbtp - input_tp)
    return min(g, settings.gain_clamp_db)


def peak_after_gain(input_tp: float, gain: float) -> float:
    return input_tp + gain


def assert_peak_safe(input_tp: float, gain: float, settings: RenderSettings, *, what: str = "master") -> None:
    """Fail closed when the gain about to be applied would leave the master over the ceiling.

    This is the invariant, stated once: `input_tp + gain_db <= tp_ceiling_dbtp`. It is checked
    on the arithmetic here and again on the artifact in render.py, because arithmetic can be
    right while the filter chain is wrong.
    """
    if not math.isfinite(input_tp) or not math.isfinite(gain):
        raise JobError(what, f"peak check has non-finite inputs: tp={input_tp!r} gain={gain!r}")
    peak = peak_after_gain(input_tp, gain)
    if peak > settings.tp_ceiling_dbtp + PEAK_TOLERANCE_DB:
        raise JobError(what, f"peak-unsafe: {peak:.3f} dBTP after {gain:.3f} dB of gain exceeds the "
                             f"{settings.tp_ceiling_dbtp} dBTP ceiling")


def is_silent(input_i: float, settings: RenderSettings) -> bool:
    return not (input_i > settings.silent_below_lufs)   # handles -inf / nan


def measured_peak_dbtp(pcm: bytes, settings: RenderSettings, timeout_s: int = 300) -> float:
    """True peak of the mastered s16 stream, measured by feeding it back through ebur128.

    The stream is already in memory, so this costs one more ffmpeg pass over data that never
    touches disk. It is the check that covers the artifact rather than the arithmetic: a wrong
    filter chain, a resampler overshoot or a bad drift correction all show up here (#27).
    """
    argv = ["ffmpeg", "-hide_banner", "-nostats", "-f", "s16le", "-ac", "2",
            "-ar", str(settings.sample_rate), "-i", "pipe:0",
            "-af", "ebur128=peak=true", "-f", "null", "-"]
    res = run(argv, timeout_s=timeout_s, what="verify peak", input_bytes=pcm)
    return parse_ebur128(res.stderr.decode("utf-8", "replace"))["input_tp"]


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
                 timeout_s: int = 600, in_args: list[str] | None = None) -> None:
    af = master_filter(gain, settings, duration_s, start_offset_s=start_offset_s, drift_ppm=drift_ppm,
                       native_rate=native_rate)
    tmp = master_flac.with_suffix(".flac.tmp")
    argv = ["ffmpeg", "-hide_banner", "-nostats", "-y", *(in_args or []), "-i", str(raw_wav), "-af", af,
            "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24",
            "-f", "flac", str(tmp)]
    run(argv, timeout_s=timeout_s, what="master")
    tmp.replace(master_flac)


def master_pcm(raw_wav: Path, gain: float, settings: RenderSettings, duration_s: int, *,
               start_offset_s: float = 0.0, drift_ppm: float = 0.0, native_rate: int = 48000,
               master_flac: Path | None = None, timeout_s: int = 600,
               in_args: list[str] | None = None) -> bytes:
    """Master filter + tier padding in ONE ffmpeg pass, returning the padded s16 stream.

    Replaces raw -> FLAC(s32/24) -> decode -> s16. The FLAC round trip was lossless but the two
    quantisation orders are not identical: measured max |delta| = 1 LSB of s16 (~ -90 dBFS) on a
    real E1M1 render. That is why PIPELINE_VERSION is 2. When `master_flac` is given the FLAC is
    written from the same invocation as a second output, so the PCM that gets encoded is byte-for-byte
    the same whether or not masters are kept.
    """
    from .encode import BYTES_PER_FRAME, padded_length_samples
    af = master_filter(gain, settings, duration_s, start_offset_s=start_offset_s, drift_ppm=drift_ppm,
                       native_rate=native_rate)
    total = padded_length_samples(settings, duration_s)
    li = settings.lead_in_samples
    pad = (f"aformat=channel_layouts=stereo,adelay={li}S|{li}S,"
           f"apad=whole_len={total},atrim=end_sample={total}")
    argv = ["ffmpeg", "-hide_banner", "-nostats", "-y", *(in_args or []), "-i", str(raw_wav)]
    tmp = None
    if master_flac is not None:
        # both outputs come off one filter graph: the FLAC from the master chain, the PCM from the
        # same chain plus tier padding. -af cannot be applied to a stream already mapped out of
        # -filter_complex, so the padding has to live inside the graph.
        tmp = master_flac.with_suffix(".flac.tmp")
        argv += ["-filter_complex", f"[0:a]{af},asplit=2[m][x];[x]{pad}[p]",
                 "-map", "[m]", "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24",
                 "-f", "flac", str(tmp),
                 "-map", "[p]"]
    else:
        argv += ["-af", af + "," + pad]
    argv += ["-f", "s16le", "-ac", "2", "-ar", str(settings.sample_rate), "-"]
    res = run(argv, timeout_s=timeout_s, what="master")
    pcm = res.stdout
    if len(pcm) != total * BYTES_PER_FRAME:
        raise JobError("master", f"padded stream is {len(pcm)} bytes, expected {total * BYTES_PER_FRAME}")
    if tmp is not None:
        tmp.replace(master_flac)
    return pcm
