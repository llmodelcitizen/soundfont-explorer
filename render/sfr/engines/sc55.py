"""Nuked-SC55 (jcmoyer fork, `nuked-sc55-render`) — Roland SC-55 family, owner-supplied ROMs.

    nuked-sc55-render -f f32 --end release -n 1 -d /roms/<romset> --romset <family> -o <tmp>/raw.wav /songs/<id>.mid

Only present in the image built with --build-arg WITH_ROM_ENGINES=1. ROM sets live in
roms/<romset>/ (one complete set per directory, any file names; detected by SHA256) and
`variant["romset"]` == that directory name == the variant id (sc55-<family>), so that
cli.available_roms() can skip variants whose ROMs are absent. The `--romset` flag value is
the family (mk2, st, mk1, …), taken from variant["rom"]["family"] or engines.json
`romset_flag[romset]`.

Output rate depends on the romset: 66207 Hz (mk2, st, cm300, scb55, rlp3237, sc155mk2) or
64000 Hz (mk1, jv880, sc155). ffmpeg reads the real rate from the WAV header for the
resample; `native_rate` below (engines.json table) only matters for the drift correction,
which is null until ROMs exist and `sfr calibrate` has run.
"""
from __future__ import annotations

from pathlib import Path

from . import RenderSpec, rom_dir_check, romset_of

FAMILIES = ("mk2", "st", "mk1", "cm300", "jv880", "scb55", "rlp3237", "sc155", "sc155mk2")


def family_of(variant: dict, eng: dict) -> str:
    rom = variant.get("rom") or {}
    fam = rom.get("family") or (eng.get("romset_flag") or {}).get(romset_of(variant, "sc55"))
    if not fam:
        rs = romset_of(variant, "sc55")
        fam = rs[len("sc55-"):] if rs.startswith("sc55-") else rs
    base = fam.split("-", 1)[0]          # allow pinned versions: mk2-v1.01
    if base not in FAMILIES:
        raise ValueError(f"unknown Nuked-SC55 romset family {fam!r}; known: {FAMILIES}")
    return fam


def native_rate_of(family: str, eng: dict) -> int:
    table = eng["native_rate"]      # per-family table (or one number)
    return int(table.get(family.split("-", 1)[0], 66207) if isinstance(table, dict) else table)


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["sc55"]
    family = family_of(job.variant, eng)
    rom_dir = paths.roms / romset_of(job.variant, "sc55")
    out = tmpdir / "raw.wav"
    argv = ["nuked-sc55-render", *eng["base_args"], "-d", str(rom_dir), "--romset", family,
            "-o", str(out), str(job.midi_path)]
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, native_rate=native_rate_of(family, eng),
                      pre=[rom_dir_check(rom_dir, f"put one complete {family} ROM set there")])
