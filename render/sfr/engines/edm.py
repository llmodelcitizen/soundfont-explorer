"""libEDMIDI via our edmidi-render front-end (render/edmidi-render/) — YM2413 OPLL + Konami SCC.

    edmidi-render -r 48000 -n 8 -f f32 -m <opll|scc|all> -o <tmp>/raw.wav /songs/<id>.mid

libEDMIDI has no chip mask: `edmidi_initEx(rate, modules)` takes an even module COUNT and
modules alternate OPLL/SCC, every melodic note sounding on both. `-m opll|scc` silences the
other chip's modules (exact decomposition, see render/edmidi-render/README.md); `-m all` is
the library's own layering. There is no PSG voice at all (emu2149 is compiled but never
attached), so a `psg` module is rejected here, before anything runs.

variant["module"] in {"opll", "scc", "all"}.
"""
from __future__ import annotations

from pathlib import Path

from . import RenderSpec

MODULES = ("opll", "scc", "all")


def module_of(variant: dict) -> str:
    m = variant.get("module") or (variant.get("render") or {}).get("module")
    if m not in MODULES:
        raise ValueError(f"edmidi variant {variant.get('id')!r}: module {m!r} not in {MODULES} "
                         "(libEDMIDI has no PSG voice; see render/edmidi-render/README.md)")
    return m


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["edmidi"]
    out = tmpdir / "raw.wav"
    argv = ["edmidi-render", *eng["base_args"], "-m", module_of(job.variant), "-o", str(out), str(job.midi_path)]
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, weight=1, timeout_s=900,
                      native_rate=int(eng.get("native_rate", 48000)))
