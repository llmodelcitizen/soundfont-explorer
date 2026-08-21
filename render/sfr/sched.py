"""Process execution and admission control.

- run(): subprocess in its own session, process-group kill on timeout, optional RLIMIT_AS.
- WeightedSemaphore: memory admission (units of 256 MB; plan §4: 64 units).
- Runner: ThreadPoolExecutor over jobs with a progress line every 10 s and append-only logs.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


# every live child process group, so an interrupt can kill them all
_LIVE: set[int] = set()
_LIVE_LOCK = threading.Lock()


def kill_all_children() -> int:
    n = 0
    with _LIVE_LOCK:
        pids = list(_LIVE)
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
            n += 1
        except ProcessLookupError:
            pass
    return n


class JobError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class Completed:
    argv: list[str]
    returncode: int
    stdout: bytes
    stderr: bytes
    seconds: float
    cpu_seconds: float


def _preexec(rlimit_as: int | None):
    def fn():
        if rlimit_as:
            import resource
            try:
                resource.setrlimit(resource.RLIMIT_AS, (rlimit_as, rlimit_as))
            except (ValueError, OSError):
                pass
    return fn


def run(argv: list[str], *, cwd: Path | None = None, timeout_s: int = 900, input_bytes: bytes | None = None,
        rlimit_as: int | None = None, env: dict[str, str] | None = None, capture: bool = True,
        check: bool = True, what: str = "") -> Completed:
    """Run argv; on timeout kill the whole process group. Raises JobError on failure."""
    if shutil.which(argv[0]) is None:
        raise JobError("missing-tool", argv[0])
    full_env = None
    if env:
        full_env = dict(os.environ)
        full_env.update(env)
    t0 = time.monotonic()
    r0 = os.times()
    # address-space limit: prefer `prlimit` (util-linux, always in Debian) over preexec_fn, which
    # CPython documents as unsafe with threads
    preexec = None
    if rlimit_as:
        if shutil.which("prlimit"):
            argv = ["prlimit", f"--as={rlimit_as}", "--", *argv]
        else:
            preexec = _preexec(rlimit_as)
    proc = subprocess.Popen(
        argv, cwd=str(cwd) if cwd else None, env=full_env,
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
        start_new_session=True, preexec_fn=preexec,
    )
    with _LIVE_LOCK:
        _LIVE.add(proc.pid)
    try:
        out, err = proc.communicate(input=input_bytes, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise JobError("timeout", f"{what or argv[0]} exceeded {timeout_s}s")
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(proc.pid)
    r1 = os.times()
    res = Completed(argv, proc.returncode, out or b"", err or b"", time.monotonic() - t0,
                    (r1.children_user - r0.children_user) + (r1.children_system - r0.children_system))
    if check and proc.returncode != 0:
        tail = (res.stderr or res.stdout)[-2000:].decode("utf-8", "replace")
        raise JobError("exit", f"{what or argv[0]} rc={proc.returncode}: {tail}")
    return res


class WeightedSemaphore:
    def __init__(self, total: int):
        self.total = total
        self.available = total
        self._cv = threading.Condition()

    def acquire(self, weight: int) -> int:
        weight = max(1, min(weight, self.total))
        with self._cv:
            while self.available < weight:
                self._cv.wait()
            self.available -= weight
        return weight

    def release(self, weight: int) -> None:
        with self._cv:
            self.available += weight
            self._cv.notify_all()


class Logs:
    def __init__(self, jobs_log: Path, errors_log: Path):
        self.jobs_log = jobs_log
        self.errors_log = errors_log
        self._lock = threading.Lock()
        jobs_log.parent.mkdir(parents=True, exist_ok=True)

    def job(self, line: str) -> None:
        with self._lock, open(self.jobs_log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")

    def error(self, key: str, reason: str, detail: str) -> None:
        with self._lock, open(self.errors_log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {key} {reason}\n")
            for ln in detail.strip().splitlines()[-20:]:
                f.write(f"    {ln}\n")


@dataclass
class Outcome:
    key: str
    status: str          # done | skipped | failed
    reason: str = ""
    seconds: float = 0.0


class Runner:
    def __init__(self, workers: int, mem_units: int, logs: Logs, progress_every: float = 10.0,
                 echo: Callable[[str], None] = print):
        self.workers = workers
        self.sem = WeightedSemaphore(mem_units)
        self.logs = logs
        self.progress_every = progress_every
        self.echo = echo
        self._lock = threading.Lock()
        self.done = self.failed = self.skipped = 0
        self.running: dict[str, float] = {}

    def _progress(self, total: int, t0: float, stop: threading.Event) -> None:
        while not stop.wait(self.progress_every):
            with self._lock:
                fin = self.done + self.failed + self.skipped
                el = time.monotonic() - t0
                rate = fin / el if el > 0 else 0
                eta = (total - fin) / rate if rate > 0 else float("inf")
                run = sorted(self.running.items(), key=lambda kv: kv[1])[:3]
                oldest = ", ".join(f"{k}({time.monotonic() - t:.0f}s)" for k, t in run)
                self.echo(f"[sfr] {fin}/{total} done={self.done} failed={self.failed} skipped={self.skipped} "
                          f"running={len(self.running)} mem_free={self.sem.available} elapsed={el:.0f}s "
                          f"eta={eta:.0f}s  {oldest}")

    interrupted = False

    def run_all(self, items: Iterable, fn: Callable[[object], Outcome]) -> list[Outcome]:
        items = list(items)
        results: list[Outcome] = []
        stop = threading.Event()
        t0 = time.monotonic()
        th = threading.Thread(target=self._progress, args=(len(items), t0, stop), daemon=True)
        th.start()
        ex = ThreadPoolExecutor(max_workers=self.workers)
        try:
            futs = {ex.submit(self._wrap, it, fn): it for it in items}
            for fut in as_completed(futs):
                results.append(fut.result())
        except KeyboardInterrupt:
            # Ctrl-C: drop everything queued, kill every engine/ffmpeg process group, report
            self.interrupted = True
            self.echo("\n[sfr] interrupted — cancelling queued jobs and killing running processes")
            ex.shutdown(wait=False, cancel_futures=True)
            killed = kill_all_children()
            self.echo(f"[sfr] killed {killed} process group(s); partial outputs are left as 'running' meta "
                      f"and will be redone on the next run")
            raise
        finally:
            stop.set()
            th.join(timeout=1)
            ex.shutdown(wait=not self.interrupted, cancel_futures=True)
        return results

    def _wrap(self, item, fn) -> Outcome:
        key = getattr(item, "key", str(item))
        with self._lock:
            self.running[key] = time.monotonic()
        try:
            oc = fn(item)
        except JobError as e:
            oc = Outcome(key, "failed", f"{e.reason}: {e.detail}")
        except Exception as e:  # noqa: BLE001 - a worker must never take the pool down
            oc = Outcome(key, "failed", f"exception: {type(e).__name__}: {e}")
        with self._lock:
            self.running.pop(key, None)
            if oc.status == "done":
                self.done += 1
            elif oc.status == "skipped":
                self.skipped += 1
            else:
                self.failed += 1
        return oc
