"""On-demand audio previews: fluidsynth fast-render piped through ffmpeg to MP3.

MP3 (CBR 128k) because it progressive-streams in every browser <audio> — Safari cannot
play ogg/opus.

Rendering is decoupled from delivery: a background job renders at full speed into a temp
file (ffmpeg writes it directly — never paced by the client), and the HTTP response tails
that growing file. The 2-slot render semaphore is therefore held for seconds per track,
not for as long as a browser keeps a stream open — the first design held a slot at the
client's pace, so two paused <audio> downloads starved every later preview (2026-08-24).
Concurrent requests for the same track join the same job. The cache is an mtime-LRU
capped at 500 MB.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time

from .config import get_config

log = logging.getLogger("sfadmin.preview")

CACHE_MAX_BYTES = 500 * 1024 * 1024
RENDER_TIMEOUT_S = 600
# how long the route waits for the render's first bytes before streaming anyway: long
# enough to turn the usual fast failure (no soundfont, unplayable MIDI) into a 500, short
# enough that a queued render cannot hold a threadpool worker for minutes (#19)
PRIME_TIMEOUT_S = 5.0
_render_slots = threading.Semaphore(2)
_sweep_lock = threading.Lock()
_active: dict[str, "Job"] = {}
_active_lock = threading.Lock()


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


class Job:
    def __init__(self, sha256: str) -> None:
        self.sha256 = sha256
        self.tmp = cache_path(sha256) + ".tmp"
        self.done = threading.Event()
        self.error: str | None = None


def ensure(midi_path: str, sha256: str) -> Job | None:
    """Start (or join) the background render for this track; None if already cached."""
    if cached(sha256):
        return None
    if not os.path.exists(get_config().gm_sf2):
        raise FileNotFoundError("no GM soundfont — upload assets/gm.sf2 (docs/ADMIN.md)")
    with _active_lock:
        job = _active.get(sha256)
        if job is None:
            job = Job(sha256)
            _active[sha256] = job
            threading.Thread(target=_render, args=(job, midi_path),
                             name=f"preview-{sha256[:8]}", daemon=True).start()
        return job


def _render(job: Job, midi_path: str) -> None:
    synth = ffmpeg = None
    try:
        with _render_slots:
            with open(job.tmp, "wb") as out:
                synth = subprocess.Popen(
                    ["fluidsynth", "-nli", "-r", "44100", "-O", "s16", "-T", "raw",
                     "-F", "/dev/stdout", get_config().gm_sf2, midi_path],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                ffmpeg = subprocess.Popen(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error",
                     "-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "pipe:0",
                     "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1"],
                    stdin=synth.stdout, stdout=out, stderr=subprocess.DEVNULL)
                synth.stdout.close()  # ffmpeg owns the pipe; fluidsynth gets SIGPIPE if it dies
                ff_rc = ffmpeg.wait(timeout=RENDER_TIMEOUT_S)
                synth_rc = synth.wait(timeout=15)
        if ff_rc != 0 or synth_rc != 0 or os.path.getsize(job.tmp) == 0:
            raise RuntimeError(f"render failed (fluidsynth={synth_rc}, ffmpeg={ff_rc})")
        os.replace(job.tmp, cache_path(job.sha256))
        _sweep()
    except Exception as e:
        job.error = str(e)
        log.warning("preview %s failed: %s", job.sha256[:12], e)
        try:
            os.remove(job.tmp)
        except FileNotFoundError:
            pass
    finally:
        for p in (synth, ffmpeg):
            if p is not None and p.poll() is None:
                p.kill()
        job.done.set()
        with _active_lock:
            _active.pop(job.sha256, None)


def follow(job: Job):
    """Generator: tail the render's output file as it grows. Never holds a render slot,
    so a paused or abandoned download costs nothing but this one connection."""
    fh = None
    try:
        while fh is None:  # the job may still be queued for a slot
            try:
                fh = open(job.tmp, "rb")
            except FileNotFoundError:
                if job.done.is_set():
                    final = cached(job.sha256)
                    if final:  # finished + renamed before we ever opened it
                        fh = open(final, "rb")
                        break
                    return  # failed before producing anything; job.error has why
                time.sleep(0.2)
        while True:
            chunk = fh.read(64 * 1024)
            if chunk:
                yield chunk
            elif job.done.is_set():
                break  # EOF and the writer is gone (rename keeps our inode valid)
            else:
                time.sleep(0.1)
    finally:
        if fh is not None:
            fh.close()


def _produced(job: Job) -> bool:
    """Has the render written anything a follower could read yet?"""
    try:
        if os.path.getsize(job.tmp) > 0:
            return True
    except OSError:
        pass
    return cached(job.sha256) is not None  # finished and renamed into place


def open_stream(job: Job, prime_s: float = PRIME_TIMEOUT_S):
    """follow(job), after a bounded wait for the render to produce something. Raises
    RuntimeError(job.error) if it failed before producing anything, so the route answers an
    error status instead of a 200 with an empty body — which is what a failed preview used
    to look like, with job.error never read (#19).

    The wait is bounded and does not block in read(): preview_mp3 is a sync FastAPI route,
    so it holds an anyio threadpool worker in one uncancellable call, and priming with
    next(follow(job)) could hold it for a queued render's whole RENDER_TIMEOUT_S — a burst
    of preview clicks then pinned the pool and a client disconnect could not break it out
    (#19). A render still queued for a slot after prime_s streams instead: the response
    starts and follow() tails it; a failure that late is a truncated body, as it always was
    once the status line is gone."""
    deadline = time.monotonic() + prime_s
    while not _produced(job):
        if job.done.is_set():
            raise RuntimeError(job.error or "render produced no output")
        if time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    return follow(job)


def doctor() -> dict:
    return {
        "fluidsynth": shutil.which("fluidsynth") is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "gm_sf2": os.path.exists(get_config().gm_sf2),
    }
