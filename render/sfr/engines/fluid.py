"""FluidSynth CLI renderer (one process per (font, song); crashes stay contained).

FluidSynth's file renderer keeps going until every voice is silent; a font with a non-decaying
voice renders for hours (34 GB observed). We therefore write headerless f32le and let the
scheduler kill the process once the output covers the song (D + 2 s) — the master is trimmed to
exactly D anyway, so nothing audible is lost."""
from __future__ import annotations

from pathlib import Path

from . import RenderSpec
from ..sched import JobError

UNIT = 256 * 1024 * 1024


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["fluidsynth"]
    src = job.variant["source"]
    font = paths.fonts / src["file"]
    nbytes = int(src.get("bytes") or 0)
    out = tmpdir / "raw.f32le"
    base = [a for a in eng["base_args"]]
    # -T raw / -O float: headerless 32-bit float at -r (base_args carry -r 48000); replace any -T wav
    if "-T" in base:
        i = base.index("-T")
        base[i + 1] = "raw"
    argv = ["fluidsynth", "-F", str(out), *base]
    if nbytes >= int(eng.get("dynamic_sample_loading_above_bytes", 128 * 1024 * 1024)):
        argv += ["-o", "synth.dynamic-sample-loading=1"]
    argv += [str(font), str(job.midi_path)]
    weight = max(1, -(-nbytes // UNIT))
    timeout = 1800 if nbytes > 1024 ** 3 else 900

    def pre():
        # fluidsynth silently renders with its bundled default GM font when a font path is
        # unreadable — that would publish the wrong audio under this variant's id
        if not font.is_file():
            raise JobError("missing-font", str(font))
        if nbytes and font.stat().st_size != nbytes:
            raise JobError("font-changed", f"{font.name}: {font.stat().st_size} bytes, catalog says {nbytes}")

    rate = int(eng.get("native_rate", 48000))
    cap = (int(job.duration_s) + 2) * rate * 2 * 4
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, weight=weight, timeout_s=timeout,
                      rlimit_as_bytes=8 * 1024 ** 3, native_rate=rate, pre=[pre],
                      raw_format=("f32le", rate, 2), max_out_bytes=cap)
