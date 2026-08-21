"""Per-job pipeline (plan §8.3): render → measure → gain → master → decode → two Opus tiers → meta."""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from . import PIPELINE_VERSION, engines
from .config import Paths, load_engines
from .jobs import Job, State, classify, now_iso, write_meta
from .loudness import gain_db, is_silent, measure, write_master
from .ogg import OggError, opus_info
from .encode import decode_padded, encode_tiers
from .sched import JobError, Outcome, WeightedSemaphore, run


def _clean_dir(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)


def _fail(job: Job, paths: Paths, reason: str, detail: str, t0: float, extra: dict | None = None) -> Outcome:
    meta = {
        "status": "failed", "reason": reason, "detail": detail[-4000:],
        "song": job.song_id, "variant": job.variant_id, "engine": job.engine, "core": job.core,
        "spec_hash": job.spec_hash, "master_hash": job.master_hash, "render_hash": job.render_hash,
        "created": now_iso(), "pipeline_version": PIPELINE_VERSION, "seconds": round(time.monotonic() - t0, 2),
    }
    if extra:
        meta.update(extra)
    write_meta(job.meta_path(paths), meta)
    return Outcome(job.key, "failed", reason, time.monotonic() - t0)


def run_job(job: Job, paths: Paths, *, sem: WeightedSemaphore | None = None, retry: bool = False,
            keep_tmp: bool = False, engines_json: dict | None = None, logs=None) -> Outcome:
    t0 = time.monotonic()
    engines_json = engines_json or load_engines(paths)
    state = classify(job, paths)
    if state == State.DONE:
        return Outcome(job.key, "skipped", "done", 0.0)
    if state == State.FAILED and not retry:
        return Outcome(job.key, "skipped", "failed-previously", 0.0)

    out_dir = job.out_dir(paths)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = job.tmp_dir(paths)
    _clean_dir(tmp)
    timings: dict[str, float] = {}
    meta: dict = {
        "status": "running", "song": job.song_id, "variant": job.variant_id, "engine": job.engine,
        "core": job.core, "engine_version": job.engine_meta.get("version"),
        "engine_commit": job.engine_meta.get("commit"),
        "spec_hash": job.spec_hash, "master_hash": job.master_hash, "render_hash": job.render_hash,
        "duration_s": job.duration_s, "n_slices": job.n_slices, "n_listen": job.n_listen,
        "encode_params": job.encode_params, "pipeline_version": PIPELINE_VERSION,
    }
    try:
        if state == State.REENCODE:
            old = job.meta_path(paths)
            from .jobs import read_meta
            prev = read_meta(old) or {}
            for k in ("lufs", "tp", "lra", "gain_db", "cmd", "render_seconds", "render_cpu_seconds",
                      "start_offset_s", "drift_ppm"):
                if k in prev:
                    meta[k] = prev[k]
            meta["reencoded_from"] = prev.get("render_hash")
        else:
            # ---- 1. render -------------------------------------------------
            spec = engines.get(job.engine).spec(job, paths, engines_json, tmp)
            weight = 0
            if sem is not None:
                weight = sem.acquire(spec.weight)
            try:
                for pre in spec.pre:
                    pre()
                tr = time.monotonic()
                res = run(spec.argv, cwd=spec.cwd, timeout_s=spec.timeout_s, rlimit_as=spec.rlimit_as_bytes,
                          env=spec.env, what=job.engine)
                timings["render_s"] = round(time.monotonic() - tr, 2)
                timings["render_cpu_s_approx"] = round(res.cpu_seconds, 2)  # process-wide children delta: over-counts under concurrency
                if not spec.out_wav.exists() or spec.out_wav.stat().st_size < 1024:
                    tail = (res.stderr or res.stdout)[-1500:].decode("utf-8", "replace")
                    raise JobError("no-output", f"{job.engine} produced no WAV: {tail}")
                # ---- 2. measure ------------------------------------------
                tm = time.monotonic()
                meas = measure(spec.out_wav, job.settings)
                timings["measure_s"] = round(time.monotonic() - tm, 2)
                meta.update({"lufs": meas["input_i"], "tp": meas["input_tp"], "lra": meas["input_lra"],
                             "cmd": " ".join(spec.argv), "render_seconds": timings["render_s"],
                             "render_cpu_seconds_approx": timings["render_cpu_s_approx"]})
                if is_silent(meas["input_i"], job.settings):
                    return _fail(job, paths, "silent", f"integrated loudness {meas['input_i']} LUFS", t0,
                                 {"lufs": meas["input_i"], "tp": meas["input_tp"]})
                # ---- 3. gain, 4. master -----------------------------------
                g = gain_db(meas["input_i"], meas["input_tp"], job.settings)
                meta["gain_db"] = round(g, 3)
                off = float(job.engine_meta.get("start_offset_s") or 0.0)
                drift = float(job.engine_meta.get("drift_ppm") or 0.0)
                meta["start_offset_s"] = off
                meta["drift_ppm"] = drift
                tms = time.monotonic()
                write_master(spec.out_wav, job.master_path(paths), g, job.settings, job.duration_s,
                             start_offset_s=off, drift_ppm=drift, native_rate=spec.native_rate)
                timings["master_s"] = round(time.monotonic() - tms, 2)
            finally:
                if sem is not None and weight:
                    sem.release(weight)
                try:
                    if spec.out_wav.exists():
                        spec.out_wav.unlink()
                except OSError:
                    pass
        # ---- 5/6. encode tiers ------------------------------------------
        te = time.monotonic()
        seg_dir, lis_dir = job.seg_dir(paths), job.listen_dir(paths)
        _clean_dir(seg_dir)
        _clean_dir(lis_dir)
        pcm = decode_padded(job.master_path(paths), job.settings, job.duration_s)
        sizes = encode_tiers(pcm, seg_dir, lis_dir, job.settings, job.duration_s)
        del pcm
        timings["encode_s"] = round(time.monotonic() - te, 2)
        # quick structural check of the first/last segment of each tier
        for p, want in ((seg_dir / "0000.opus", job.settings.segment_samples),
                        (seg_dir / f"{job.n_slices - 1:04d}.opus", job.settings.segment_samples),
                        (lis_dir / "000.opus", job.settings.listen_segment_samples),
                        (lis_dir / f"{job.n_listen - 1:03d}.opus", job.settings.listen_segment_samples)):
            try:
                info = opus_info(p)
            except OggError as e:
                raise JobError("verify", f"{p.name}: {e}")
            if info.samples != want:
                raise JobError("verify", f"{p.name} has {info.samples} samples, expected {want}")
        meta.update({
            "status": "ok", "timings": timings, "sizes": {"seg_bytes": sum(sizes["seg"]),
                                                          "listen_bytes": sum(sizes["listen"]),
                                                          "master_bytes": job.master_path(paths).stat().st_size},
            "created": now_iso(), "seconds": round(time.monotonic() - t0, 2),
        })
        write_meta(job.meta_path(paths), meta)
        if logs:
            logs.job(f"ok {job.key} {meta['seconds']}s lufs={meta.get('lufs')} gain={meta.get('gain_db')}")
        return Outcome(job.key, "done", "", time.monotonic() - t0)
    except JobError as e:
        if logs:
            logs.error(job.key, e.reason, e.detail)
            logs.job(f"failed {job.key} {e.reason}")
        return _fail(job, paths, e.reason, e.detail, t0)
    finally:
        if not keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
