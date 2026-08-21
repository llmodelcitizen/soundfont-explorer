"""libOPNMIDI (opnmidiplay, WAVE_ONLY build) — YM2612 OPN2 / YM2608 OPNA.

Facts probed in the image (render/engines.json "opnmidi", docs/RENDER.md):
  * no -w/-nl: always writes `<input>.wav` next to the input, never loops, 44100 Hz fixed
    -> symlink the song into the job scratch dir and run there (same trick as adl.py);
  * argument order is OPTIONS first, then the bank path, then the MIDI (both positional,
    there is no -b); the parser stops at the first unknown token and treats it as the
    bank, so an unknown --emu-X fails loudly ("Custom bank: Invalid data stream!", rc 2);
  * `--chips N`: one chip = 6 FM voices (engines.json uses 2).

Variants reference their bank as
    variant["bank"] = {"kind": "file", "dir": "wopn", "file": "<slug>.wopn", "sha256": ...}
which resolves to <paths.banks>/wopn/<file> (/opt/banks/wopn in the image).
"""
from __future__ import annotations

from pathlib import Path

from . import RenderSpec, core_flag, symlinked_input

CORE_FLAGS = {
    "nuked-3438": "--emu-nuked-3438",
    "nuked-2612": "--emu-nuked-2612",
    "mame": "--emu-mame",
    "gens": "--emu-gens",
    "ymfm-opn2": "--emu-ymfm-opn2",
    "np2": "--emu-np2",
    "mame-opna": "--emu-mame-opna",
    "ymfm-opna": "--emu-ymfm-opna",
}


def bank_path(variant: dict, paths) -> Path:
    bank = variant.get("bank") or {}
    if bank.get("kind") != "file" or not bank.get("file"):
        raise ValueError(f"opnmidi variant {variant.get('id')!r} needs bank={{kind:'file', file:'<name>.wopn'}}")
    name = bank["file"]
    if "/" in name or name in ("", ".", ".."):
        raise ValueError(f"bad bank file name {name!r}")
    return paths.banks / bank.get("dir", "wopn") / name


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["opnmidi"]
    link, out, pre = symlinked_input(job, tmpdir)
    argv = ["opnmidiplay", *eng["base_args"], eng["chips_flag"], str(int(eng["chips"])),
            core_flag("opnmidi", CORE_FLAGS, job.core, engines_json), str(bank_path(job.variant, paths)), link.name]
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, pre=[pre], native_rate=int(eng["native_rate"]))
