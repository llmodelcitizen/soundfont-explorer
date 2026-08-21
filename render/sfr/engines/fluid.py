"""FluidSynth CLI renderer (one process per (font, song); crashes stay contained)."""
from __future__ import annotations

from pathlib import Path

from . import RenderSpec

UNIT = 256 * 1024 * 1024


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["fluidsynth"]
    src = job.variant["source"]
    font = paths.fonts / src["file"]
    nbytes = int(src.get("bytes") or 0)
    out = tmpdir / "raw.wav"
    argv = ["fluidsynth", "-F", str(out), *eng["base_args"]]
    if nbytes >= int(eng.get("dynamic_sample_loading_above_bytes", 128 * 1024 * 1024)):
        argv += ["-o", "synth.dynamic-sample-loading=1"]
    argv += [str(font), str(job.midi_path)]
    weight = max(1, -(-nbytes // UNIT))
    timeout = 1800 if nbytes > 1024 ** 3 else 900
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, weight=weight, timeout_s=timeout,
                      rlimit_as_bytes=8 * 1024 ** 3, native_rate=48000)
