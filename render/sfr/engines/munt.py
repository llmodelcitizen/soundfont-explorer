"""Munt (`mt32emu-smf2wav`) — Roland MT-32 / CM-32L, owner-supplied ROMs.

    mt32emu-smf2wav --quiet -f --src-quality 3 --renderer-type 1 --output-sample-format 1 \
        --record-max-start-silence -1 --record-max-end-silence -1 \
        -m /roms/<romset> -i <mt32|cm32l> -o <tmp>/raw.wav /songs/<id>.mid

Only present in the image built with --build-arg WITH_ROM_ENGINES=1. ROMs live in
roms/<romset>/ (`variant["romset"]` == directory name == variant id: mt32, cm32l; files are
identified by SHA-1, the directory is scanned non-recursively). `variant["model"]` is the
`-i` machine (mt32 | cm32l, or a pinned control-ROM version such as mt32_1_07).

Load-bearing flags (docs/RENDER.md): `--record-max-start-silence -1` (the default 0 trims
leading silence and would break start calibration) and the LONG `--src-quality` (the short
`-q` is declared twice by smf2wav). Native rate 32000 Hz with the default analog-output
mode 0; ffmpeg/soxr resamples.
"""
from __future__ import annotations

from pathlib import Path

from . import RenderSpec, rom_dir_check, romset_of

MODELS = ("mt32", "cm32l")


def model_of(variant: dict) -> str:
    m = variant.get("model") or (variant.get("rom") or {}).get("machine")
    if not m or m.split("_", 1)[0] not in MODELS:
        raise ValueError(f"munt variant {variant.get('id')!r}: model {m!r} must be mt32 or cm32l (optionally _<version>)")
    return m


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["munt"]
    base = list(eng["base_args"])
    if "--record-max-start-silence" not in base:
        raise ValueError("munt base_args must contain --record-max-start-silence -1 (start calibration depends on it)")
    model = model_of(job.variant)
    rom_dir = paths.roms / romset_of(job.variant, "munt")
    out = tmpdir / "raw.wav"
    argv = ["mt32emu-smf2wav", *base, "-m", str(rom_dir), "-i", model, "-o", str(out), str(job.midi_path)]
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, native_rate=int(eng["native_rate"]),
                      pre=[rom_dir_check(rom_dir, f"put the {model} control + PCM ROMs there")])
