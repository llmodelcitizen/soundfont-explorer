"""Inter-engine timing calibration (plan §8.3 step 4, risk table).

Render calib/click.mid (woodblock clicks at 0 s, 1 s, 169 s and 170 s) with each engine, resample
to 48 kHz, detect click onsets and compare with FluidSynth (reference, offset 0):

  start_offset_s = onset_e(1 s)  − onset_ref(1 s)
  drift_ppm      = ((onset_e(170) − onset_e(1)) − (onset_ref(170) − onset_ref(1))) / 169 s · 1e6

The clicks at 0 s and 169 s are primers: the OPLL (libEDMIDI) restarts an envelope from its residual
level when a key-on follows a recent note, so a click preceded by another one 1 s earlier has a
different attack shape from one that starts from silence. Measuring the 1 s and 170 s clicks, each
with the same 1-s-old predecessor, compares like with like (first attempt without the 169 s primer
showed a spurious 3.7 ms "drift" on libEDMIDI).

Why the 1 s click and not the 0 s one (measured 2026-08-21, docs/validation.md "M2b calibration"):
the libADLMIDI/libOPNMIDI Nuked cores queue the GM-reset + program-setup register flood ahead of the
very first key-on in the chip's write buffer, so the click AT 0 s comes out 31 ms (OPL3) / 10 ms (OPN2)
late while every later event is on time. That is a startup transient of the emulated chip, not an
offset; compensating it would shift the whole song. It is reported as `first_click_extra_ms`.

Onset = first sample at or above ONSET_FRAC of the local peak (−34 dB): close to the key-on even for
slow-attack patches (the OPLL's envelope clock quantises onsets to ~1.4 ms, so expect ±1 ms).
Pure Python, no numpy.
"""
from __future__ import annotations

import array
import json
import shutil
from pathlib import Path

from . import engines
from .config import Paths, RenderSettings
from .jobs import Job
from .midi import click_track
from .sched import run

CLICKS = (0.0, 1.0, 169.0, 170.0)
START_IDX, END_IDX = 1, 3          # clicks used for start_offset_s / drift_ppm (each preceded by a primer click 1 s earlier)
ONSET_FRAC = 0.02


def _mono_48k(wav: Path) -> array.array:
    res = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav), "-af",
               "aresample=48000:resampler=soxr:precision=28,pan=mono|c0=0.5*c0+0.5*c1",
               "-f", "s16le", "-ac", "1", "-ar", "48000", "-"], timeout_s=300, what="calib decode")
    a = array.array("h")
    a.frombytes(res.stdout[: len(res.stdout) // 2 * 2])
    return a


def onset(samples: array.array, center_s: float, *, before_s: float = 0.1, after_s: float = 0.5,
          frac: float = ONSET_FRAC, sr: int = 48000) -> float | None:
    """Time of the first sample ≥ frac·(local peak) in [center−before, center+after]; None if silent."""
    lo = max(0, int((center_s - before_s) * sr))
    hi = min(len(samples), int((center_s + after_s) * sr))
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


def analyse(onsets: dict[str, list[float | None]], ref_id: str = "fluidsynth") -> dict[str, dict]:
    """Pure function over the detected onsets (testable without engines)."""
    ref = onsets.get(ref_id)
    if not ref or None in ref:
        raise SystemExit("calibrate: FluidSynth reference click not detected")
    ref_start, ref_end = ref[START_IDX], ref[END_IDX]
    ref_span = ref_end - ref_start
    nominal_span = CLICKS[END_IDX] - CLICKS[START_IDX]
    results: dict[str, dict] = {}
    for eid, ons in onsets.items():
        if len(ons) != len(CLICKS) or ons[START_IDX] is None or ons[END_IDX] is None:
            results[eid] = {"error": "click not detected", "onsets": ons}
            continue
        o_start, o_end = ons[START_IDX], ons[END_IDX]
        off = o_start - ref_start
        end_drift = (o_end - o_start) - ref_span
        r = {"start_offset_s": round(off, 5), "drift_ppm": round(end_drift / ref_span * 1e6, 1),
             "end_drift_ms": round(end_drift * 1000, 3), "onsets": ons,
             "clicks_s": list(CLICKS), "measured_span_s": round(o_end - o_start, 6), "nominal_span_s": nominal_span}
        if ons[0] is not None:
            # how much later than its steady-state timing the very first click arrives (startup transient)
            r["first_click_extra_ms"] = round((ons[0] - (o_start - CLICKS[START_IDX])) * 1000, 3)
        results[eid] = r
    return results


def calibrate(paths: Paths, settings: RenderSettings, engines_json: dict, variants_by_engine: dict[str, dict],
              *, echo=print) -> dict:
    """variants_by_engine: one representative variant dict per engine id (fluidsynth must be present)."""
    cdir = paths.work / "calib"
    cdir.mkdir(parents=True, exist_ok=True)
    mid = cdir / "click.mid"
    click_track(mid, CLICKS, tail_s=3.0)
    duration = int(CLICKS[-1]) + 4
    song = {"id": "click", "_dir": cdir, "duration_s": duration, "sha256": "calib"}
    onsets: dict[str, list[float | None]] = {}
    for eid, variant in variants_by_engine.items():
        emeta = engines_json["engines"][eid]
        job = Job(song, variant, settings, emeta)
        tmp = cdir / eid
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        spec = engines.get(eid).spec(job, paths, engines_json, tmp)
        for pre in spec.pre:
            pre()
        run(spec.argv, cwd=spec.cwd, timeout_s=spec.timeout_s, what=eid)
        mono = _mono_48k(spec.out_wav)
        onsets[eid] = [onset(mono, c) for c in CLICKS]
        echo(f"[calib] {eid}: onsets {onsets[eid]}")
    results = analyse(onsets)
    (cdir / "report.json").write_text(json.dumps(results, indent=1))
    return results


def apply_to_engines_json(path: Path, results: dict, *, drift_threshold_ms: float = 2.0,
                          max_offset_s: float = 0.5) -> list[str]:
    """Write start_offset_s / drift_ppm for every engine with a sane result; returns the engine ids written."""
    data = json.loads(path.read_text())
    written = []
    for eid, r in results.items():
        if "start_offset_s" not in r or abs(r["start_offset_s"]) > max_offset_s:
            continue
        e = data["engines"][eid]
        e["start_offset_s"] = r["start_offset_s"]
        e["drift_ppm"] = r["drift_ppm"] if abs(r["end_drift_ms"]) > drift_threshold_ms else 0
        written.append(eid)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return written
