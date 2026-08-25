"""Per-job pipeline (plan §8.3): render → measure → gain → master → decode → two Opus tiers → meta."""
from __future__ import annotations

import shutil
import time
import traceback

from . import PIPELINE_VERSION, engines
from .engines import ffmpeg_input_args
from .config import Paths, load_engines
from .jobs import Job, State, classify, clean_dir, now_iso, read_meta, write_meta
from .loudness import gain_db, is_silent, master_pcm, measure
from .ogg import OggError, opus_info
from .encode import decode_padded, encode_tiers
from .sched import JobError, Outcome, WeightedSemaphore, run


def _fail(job: Job, paths: Paths, meta: dict, reason: str, detail: str, t0: float) -> Outcome:
    """Write the in-progress meta as failed (whatever was measured so far stays for diagnosis)."""
    meta.update({"status": "failed", "reason": reason, "detail": detail[-4000:],
                 "created": now_iso(), "seconds": round(time.monotonic() - t0, 2)})
    write_meta(job.meta_path(paths), meta)
    return Outcome(job.key, "failed", reason, time.monotonic() - t0)


def run_job(job: Job, paths: Paths, *, sem: WeightedSemaphore | None = None, retry: bool = False,
            keep_tmp: bool = False, keep_masters: bool = False, engines_json: dict | None = None,
            logs=None) -> Outcome:
    t0 = time.monotonic()
    engines_json = engines_json or load_engines(paths)
    keep_masters = bool(keep_masters)
    state = classify(job, paths)
    if state == State.DONE:
        return Outcome(job.key, "skipped", "done", 0.0)
    if state == State.FAILED and not retry:
        return Outcome(job.key, "skipped", "failed-previously", 0.0)

    out_dir = job.out_dir(paths)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = job.tmp_dir(paths)
    clean_dir(tmp)
    timings: dict[str, float] = {}
    meta: dict = {
        "status": "running", "song": job.song_id, "variant": job.variant_id, "engine": job.engine,
        "core": job.core, "engine_version": job.engine_meta.get("version"),
        "engine_commit": job.engine_meta.get("commit"),
        "spec_hash": job.spec_hash, "master_hash": job.master_hash, "render_hash": job.render_hash,
        "duration_s": job.duration_s, "n_slices": job.n_slices, "n_listen": job.n_listen,
        "encode_params": job.encode_params, "pipeline_version": PIPELINE_VERSION,
        "master_kept": keep_masters,
    }
    pcm: bytes | None = None
    try:
        if state == State.REENCODE:
            prev = read_meta(job.meta_path(paths)) or {}
            for k in ("lufs", "tp", "lra", "gain_db", "cmd", "render_seconds", "render_cpu_seconds",
                      "start_offset_s", "drift_ppm"):
                if k in prev:
                    meta[k] = prev[k]
            meta["reencoded_from"] = prev.get("render_hash")
            # the master on disk is this meta's master (classify() vouched for it and it is the
            # decode source below) whatever --keep-masters says this run; without this, a plain
            # re-encode would disown it and the next one would have to re-render (#13)
            meta["master_kept"] = True
        else:
            # a master.flac here belongs to an earlier spec or to a run that never finished
            # (classify() would have chosen REENCODE otherwise); left behind by a non-keep
            # render it became a REENCODE source once master_hash matched, so an encode change
            # re-encoded stale audio (#13). --keep-masters rewrites it in master_pcm().
            job.master_path(paths).unlink(missing_ok=True)
            # ---- 1. render -------------------------------------------------
            spec = engines.get(job.engine).spec(job, paths, engines_json, tmp)
            weight = 0
            if sem is not None:
                weight = sem.acquire(spec.weight)
            try:
                for pre in spec.pre:
                    pre()
                tr = time.monotonic()
                stop_when = (spec.out_wav, spec.max_out_bytes) if spec.max_out_bytes else None
                res = run(spec.argv, cwd=spec.cwd, timeout_s=spec.timeout_s, rlimit_as=spec.rlimit_as_bytes,
                          what=job.engine, stop_when=stop_when)
                if res.truncated:
                    meta["render_truncated"] = True   # engine would not stop on its own (non-decaying voice)
                timings["render_s"] = round(time.monotonic() - tr, 2)
                timings["render_cpu_s_approx"] = round(res.cpu_seconds, 2)  # process-wide children delta: over-counts under concurrency
                if not spec.out_wav.exists() or spec.out_wav.stat().st_size < 1024:
                    tail = (res.stderr or res.stdout)[-1500:].decode("utf-8", "replace")
                    raise JobError("no-output", f"{job.engine} produced no WAV: {tail}")
                # ---- 2. measure ------------------------------------------
                tm = time.monotonic()
                meas = measure(spec.out_wav, job.settings, in_args=ffmpeg_input_args(spec))
                timings["measure_s"] = round(time.monotonic() - tm, 2)
                meta.update({"lufs": meas["input_i"], "tp": meas["input_tp"], "lra": meas["input_lra"],
                             "cmd": " ".join(spec.argv), "render_seconds": timings["render_s"],
                             "render_cpu_seconds_approx": timings["render_cpu_s_approx"]})
                if is_silent(meas["input_i"], job.settings):
                    if logs:
                        logs.job(f"failed {job.key} silent ({meas['input_i']} LUFS)")
                    return _fail(job, paths, meta, "silent", f"integrated loudness {meas['input_i']} LUFS", t0)
                # ---- 3. gain, 4. master -----------------------------------
                g = gain_db(meas["input_i"], meas["input_tp"], job.settings)
                meta["gain_db"] = round(g, 3)
                off = float(job.engine_meta.get("start_offset_s") or 0.0)
                drift = float(job.engine_meta.get("drift_ppm") or 0.0)
                meta["start_offset_s"] = off
                meta["drift_ppm"] = drift
                tms = time.monotonic()
                pcm = master_pcm(spec.out_wav, g, job.settings, job.duration_s,
                                 start_offset_s=off, drift_ppm=drift, native_rate=spec.native_rate,
                                 master_flac=job.master_path(paths) if keep_masters else None,
                                 in_args=ffmpeg_input_args(spec))
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
        clean_dir(seg_dir)
        clean_dir(lis_dir)
        if pcm is None:   # REENCODE: audio unchanged, encode recipe changed → decode the kept master
            pcm = decode_padded(job.master_path(paths), job.settings, job.duration_s)
        sizes = encode_tiers(pcm, seg_dir, lis_dir, job.settings, job.duration_s,
                             serial_seed=job.render_hash)
        del pcm
        timings["encode_s"] = round(time.monotonic() - te, 2)
        # quick structural check of the first/last segment of each tier
        files = job.segment_files(paths)
        n = job.n_slices
        for p, want in (files[0], files[n - 1], files[n], files[-1]):
            try:
                info = opus_info(p)
            except OggError as e:
                raise JobError("verify", f"{p.name}: {e}")
            if info.samples != want:
                raise JobError("verify", f"{p.name} has {info.samples} samples, expected {want}")
        meta.update({
            "status": "ok", "timings": timings, "sizes": {
                "seg_bytes": sum(sizes["seg"]), "listen_bytes": sum(sizes["listen"]),
                "master_bytes": job.master_path(paths).stat().st_size
                if job.master_path(paths).exists() else 0},
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
        return _fail(job, paths, meta, e.reason, e.detail, t0)
    except Exception as e:  # noqa: BLE001 — a bug must still leave a failed meta + a log line
        detail = traceback.format_exc()
        if logs:
            logs.error(job.key, "exception", detail)
            logs.job(f"failed {job.key} exception {type(e).__name__}")
        return _fail(job, paths, meta, "exception", detail, t0)
    finally:
        if not keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
