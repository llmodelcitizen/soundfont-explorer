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

from . import RenderSpec
from ..sched import JobError

FAMILIES = ("mk2", "st", "mk1", "cm300", "jv880", "scb55", "rlp3237", "sc155", "sc155mk2")


def romset_of(variant: dict) -> str:
    rs = variant.get("romset")
    if not rs or "/" in rs:
        raise ValueError(f"sc55 variant {variant.get('id')!r} needs romset=<directory under roms/>")
    return rs


def family_of(variant: dict, eng: dict) -> str:
    rom = variant.get("rom") or {}
    fam = rom.get("family") or (eng.get("romset_flag") or {}).get(romset_of(variant))
    if not fam:
        rs = romset_of(variant)
        fam = rs[len("sc55-"):] if rs.startswith("sc55-") else rs
    base = fam.split("-", 1)[0]          # allow pinned versions: mk2-v1.01
    if base not in FAMILIES:
        raise ValueError(f"unknown Nuked-SC55 romset family {fam!r}; known: {FAMILIES}")
    return fam


def native_rate_of(family: str, eng: dict) -> int:
    table = eng.get("native_rate")
    if isinstance(table, dict):
        return int(table.get(family.split("-", 1)[0], 66207))
    return int(table or 66207)


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["sc55"]
    romset = romset_of(job.variant)
    family = family_of(job.variant, eng)
    rom_dir = paths.roms / romset
    out = tmpdir / "raw.wav"

    def pre():
        if not rom_dir.is_dir():
            raise JobError("missing-rom", f"{rom_dir} does not exist (put one complete {family} ROM set there)")

    argv = ["nuked-sc55-render", *eng["base_args"], "-d", str(rom_dir), "--romset", family,
            "-o", str(out), str(job.midi_path)]
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, weight=1, timeout_s=900, pre=[pre],
                      native_rate=native_rate_of(family, eng))
