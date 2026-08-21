"""libADLMIDI (adlmidiplay, WAVE_ONLY build).

The image's adlmidiplay is built with MIDIPLAY_WAVE_ONLY: it has no -w/-nl flags,
always writes `<input>.wav` next to the input and never loops, at 44100 Hz.
We symlink the song into the job's scratch dir and run there (MVP trick).
"""
from __future__ import annotations

import os
from pathlib import Path

from . import RenderSpec

CORE_FLAGS = {
    "nuked": "--emu-nuked",
    "nuked7": "--emu-nuked7",
    "dosbox": "--emu-dosbox",
    "dosbox-opl2": "--emu-dosbox-opl2",
    "nuked-opl2": "--emu-nuked-opl2",
    "nuked-cqm": "--emu-nuked-cqm",
    "esfmu": "--emu-esfmu",
    "opal": "--emu-opal",
    "java": "--emu-java",
    "mame-opl2": "--emu-mame-opl2",
    "ymfm-opl2": "--emu-ymfm-opl2",
    "ymfm-opl3": "--emu-ymfm-opl3",
}


def core_flag(core: str) -> str:
    try:
        return CORE_FLAGS[core]
    except KeyError:
        raise ValueError(f"unknown libADLMIDI core {core!r}; known: {sorted(CORE_FLAGS)}") from None


def bank_arg(variant: dict, paths) -> str:
    bank = variant.get("bank") or {}
    if bank.get("kind", "embedded") == "embedded":
        return str(int(bank["number"]))
    # external WOPL: path inside the image's bank dir
    return str(paths.banks / "wopl" / bank["file"])


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["adlmidi"]
    midi_src = job.midi_path
    link = tmpdir / midi_src.name
    out = tmpdir / (midi_src.name + ".wav")

    def pre():
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(midi_src, link)

    argv = ["adlmidiplay", link.name, *eng["base_args"], core_flag(job.core), bank_arg(job.variant, paths), str(eng.get("chips", 1))]
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, weight=1, timeout_s=900,
                      pre=[pre], native_rate=int(eng.get("native_rate", 44100)))
