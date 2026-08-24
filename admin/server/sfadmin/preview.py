"""On-demand audio previews: fluidsynth fast-render piped through ffmpeg to MP3.

MP3 (CBR 128k) because it progressive-streams in every browser <audio> — Safari cannot
play ogg/opus. Cache hits serve a plain file (instant, seekable); misses stream chunks as
they are encoded while tee-ing to the cache, so playback starts ~1 s after the click even
for a long song. Two concurrent renders max (the box has 2 vCPUs); cached serves are
unbounded. The cache is an mtime-LRU capped at 500 MB.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading

from .config import get_config

CACHE_MAX_BYTES = 500 * 1024 * 1024
_render_slots = threading.Semaphore(2)
_sweep_lock = threading.Lock()


def cache_dir() -> str:
    d = os.path.join(get_config().cache, "preview")
    os.makedirs(d, exist_ok=True)
    return d


def cache_path(sha256: str) -> str:
    return os.path.join(cache_dir(), f"{sha256}.mp3")


def cached(sha256: str) -> str | None:
    p = cache_path(sha256)
    if os.path.exists(p):
        os.utime(p)  # LRU touch
        return p
    return None


def _sweep() -> None:
    with _sweep_lock:
        d = cache_dir()
        files = [(os.path.getmtime(os.path.join(d, f)), os.path.join(d, f))
                 for f in os.listdir(d) if f.endswith(".mp3")]
        total = sum(os.path.getsize(p) for _, p in files)
        for _, p in sorted(files):
            if total <= CACHE_MAX_BYTES:
                break
            total -= os.path.getsize(p)
            os.remove(p)


def stream(midi_path: str, sha256: str):
    """Generator of MP3 chunks; writes the cache file atomically on clean completion."""
    if not os.path.exists(get_config().gm_sf2):
        raise FileNotFoundError("no GM soundfont — upload assets/gm.sf2 (docs/ADMIN.md)")
    with _render_slots:
        tmp = cache_path(sha256) + ".tmp"
        synth = ffmpeg = None
        try:
            synth = subprocess.Popen(
                ["fluidsynth", "-nli", "-r", "44100", "-O", "s16", "-T", "raw",
                 "-F", "/dev/stdout", get_config().gm_sf2, midi_path],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            ffmpeg = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0",
                 "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1"],
                stdin=synth.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            synth.stdout.close()  # let ffmpeg own the pipe; fluidsynth gets SIGPIPE on abort
            with open(tmp, "wb") as cache_fh:
                while True:
                    chunk = ffmpeg.stdout.read(64 * 1024)
                    if not chunk:
                        break
                    cache_fh.write(chunk)
                    yield chunk
            if ffmpeg.wait() == 0 and synth.wait() == 0 and os.path.getsize(tmp) > 0:
                os.replace(tmp, cache_path(sha256))
                _sweep()
            else:
                os.remove(tmp)
        except GeneratorExit:  # client went away mid-stream
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        finally:
            for p in (synth, ffmpeg):
                if p is not None and p.poll() is None:
                    p.kill()


def doctor() -> dict:
    return {
        "fluidsynth": shutil.which("fluidsynth") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "gm_sf2": os.path.exists(get_config().gm_sf2),
    }
