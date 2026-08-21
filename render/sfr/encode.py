"""Decode the master once to a padded PCM stream, then cut both tiers (plan §8.3 steps 5–6).

Padded stream = lead_in silence + master + padding so that every slice k of each tier is
exactly `segment_samples` long and starts at k·slice_samples of the padded stream:
slice k therefore contains lead_in_s of audio *before* its nominal start. All slices come
from one master, so overlaps are bit-identical → phase-perfect seam crossfades.
"""
from __future__ import annotations

from pathlib import Path

from .config import RenderSettings
from .sched import JobError, run

BYTES_PER_FRAME = 4   # s16le stereo


def padded_length_samples(settings: RenderSettings, duration_s: int) -> int:
    sr = settings.sample_rate
    n_scrub = settings.n_slices(duration_s)
    n_listen = settings.n_listen(duration_s)
    need = max(n_scrub * settings.slice_s * sr, n_listen * settings.listen_slice_s * sr)
    return settings.lead_in_samples + need + settings.lead_out_samples


def decode_padded(master_flac: Path, settings: RenderSettings, duration_s: int, timeout_s: int = 300) -> bytes:
    total = padded_length_samples(settings, duration_s)
    li = settings.lead_in_samples
    af = f"aformat=channel_layouts=stereo,adelay={li}S|{li}S,apad=whole_len={total},atrim=end_sample={total}"
    argv = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(master_flac), "-af", af,
            "-f", "s16le", "-ac", "2", "-ar", str(settings.sample_rate), "-"]
    res = run(argv, timeout_s=timeout_s, what="decode master")
    pcm = res.stdout
    if len(pcm) != total * BYTES_PER_FRAME:
        raise JobError("decode", f"padded stream is {len(pcm)} bytes, expected {total * BYTES_PER_FRAME}")
    return pcm


def slice_bytes(pcm: bytes, start_sample: int, n_samples: int) -> bytes:
    a = start_sample * BYTES_PER_FRAME
    b = a + n_samples * BYTES_PER_FRAME
    if b > len(pcm):
        raise JobError("slice", f"slice [{start_sample},{start_sample + n_samples}) exceeds stream")
    return pcm[a:b]


def opusenc_argv(bitrate_kbps: int, settings: RenderSettings, out: Path) -> list[str]:
    return ["opusenc", "--quiet", "--raw", "--raw-rate", str(settings.sample_rate), "--raw-chan", "2",
            "--raw-bits", "16", "--bitrate", str(bitrate_kbps), "--vbr",
            "--comp", str(settings.opus_comp), "--framesize", str(settings.opus_framesize_ms),
            "--discard-comments", "--discard-pictures", "-", str(out)]


def encode_segment(chunk: bytes, out: Path, bitrate_kbps: int, settings: RenderSettings, timeout_s: int = 300) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".opus.tmp")
    run(opusenc_argv(bitrate_kbps, settings, tmp), input_bytes=chunk, timeout_s=timeout_s, what=f"opusenc {out.name}")
    tmp.replace(out)


def encode_tiers(pcm: bytes, seg_dir: Path, listen_dir: Path, settings: RenderSettings, duration_s: int) -> dict:
    sr = settings.sample_rate
    sizes = {"seg": [], "listen": []}
    n = settings.n_slices(duration_s)
    for i in range(n):
        out = seg_dir / f"{i:04d}.opus"
        chunk = slice_bytes(pcm, i * settings.slice_s * sr, settings.segment_samples)
        encode_segment(chunk, out, settings.scrub_bitrate_kbps, settings)
        sizes["seg"].append(out.stat().st_size)
    m = settings.n_listen(duration_s)
    for k in range(m):
        out = listen_dir / f"{k:03d}.opus"
        chunk = slice_bytes(pcm, k * settings.listen_slice_s * sr, settings.listen_segment_samples)
        encode_segment(chunk, out, settings.listen_bitrate_kbps, settings)
        sizes["listen"].append(out.stat().st_size)
    return sizes
