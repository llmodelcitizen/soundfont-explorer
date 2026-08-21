"""Inter-engine timing calibration (plan §8.3 step 4, risk table).

Render calib/click.mid (clicks at 0 s and 170 s) with each engine, resample to 48 kHz,
detect click onsets and compare with FluidSynth (reference, offset 0):
  start_offset_s = onset_e(0) − onset_ref(0)
  drift_ppm      = ((onset_e(170) − onset_e(0)) − (onset_ref(170) − onset_ref(0))) / 170 s · 1e6
Pure-Python onset detection (first sample over 10 % of the local peak), no numpy.
"""
from __future__ import annotations

import array
import json
from pathlib import Path

from . import engines
from .config import Paths, RenderSettings
from .jobs import Job
from .midi import click_track
from .sched import run

CLICKS = (0.0, 170.0)


def _mono_48k(wav: Path) -> array.array:
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af",
               "aresample=48000:resampler=soxr:precision=28,pan=mono|c0=0.5*c0+0.5*c1",
               "-f", "s16le", "-ac", "1", "-ar", "48000", "-"], timeout_s=300, what="calib decode")
    a = array.array("h")
    a.frombytes(res.stdout[: len(res.stdout) // 2 * 2])
    return a


def onset(samples: array.array, center_s: float, window_s: float = 0.6, frac: float = 0.1, sr: int = 48000) -> float | None:
    lo = max(0, int((center_s - window_s / 2) * sr))
    hi = min(len(samples), int((center_s + window_s) * sr))
    if hi <= lo:
        return None
    peak = max(abs(int(x)) for x in samples[lo:hi])
    if peak < 200:   # ~ -44 dBFS: no click here
        return None
    thr = peak * frac
    for i in range(lo, hi):
        if abs(int(samples[i])) >= thr:
            return i / sr
    return None


def calibrate(paths: Paths, settings: RenderSettings, engines_json: dict, variants_by_engine: dict[str, dict],
              *, echo=print) -> dict:
    """variants_by_engine: one representative variant dict per engine id (fluidsynth must be present)."""
    cdir = paths.work / "calib"
    cdir.mkdir(parents=True, exist_ok=True)
    mid = cdir / "click.mid"
    click_track(mid, CLICKS, tail_s=3.0)
    song = {"id": "click", "_dir": cdir, "duration_s": 174, "sha256": "calib"}
    results: dict[str, dict] = {}
    onsets: dict[str, list[float | None]] = {}
    for eid, variant in variants_by_engine.items():
        emeta = engines_json["engines"][eid]
        job = Job(song, variant, settings, emeta)
        tmp = cdir / eid
        if tmp.exists():
            import shutil
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        spec = engines.get(eid).spec(job, paths, engines_json, tmp)
        for pre in spec.pre:
            pre()
        run(spec.argv, cwd=spec.cwd, timeout_s=spec.timeout_s, what=eid)
        mono = _mono_48k(spec.out_wav)
        onsets[eid] = [onset(mono, c) for c in CLICKS]
        echo(f"[calib] {eid}: onsets {onsets[eid]}")
    ref = onsets.get("fluidsynth")
    if not ref or None in ref:
        raise SystemExit("calibrate: FluidSynth reference click not detected")
    ref_span = ref[1] - ref[0]
    for eid, (o0, o1) in onsets.items():
        if o0 is None or o1 is None:
            results[eid] = {"error": "click not detected", "onsets": [o0, o1]}
            continue
        off = o0 - ref[0]
        drift = ((o1 - o0) - ref_span) / ref_span * 1e6
        results[eid] = {"start_offset_s": round(off, 5), "drift_ppm": round(drift, 1),
                        "end_drift_ms": round(((o1 - o0) - ref_span) * 1000, 3), "onsets": [o0, o1]}
    (cdir / "report.json").write_text(json.dumps(results, indent=1))
    return results


def apply_to_engines_json(path: Path, results: dict, *, drift_threshold_ms: float = 2.0) -> None:
    data = json.loads(path.read_text())
    for eid, r in results.items():
        if "start_offset_s" not in r:
            continue
        e = data["engines"][eid]
        e["start_offset_s"] = r["start_offset_s"]
        e["drift_ppm"] = r["drift_ppm"] if abs(r["end_drift_ms"]) > drift_threshold_ms else 0
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
