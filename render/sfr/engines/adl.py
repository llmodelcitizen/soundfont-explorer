"""libADLMIDI (adlmidiplay, WAVE_ONLY build).

The image's adlmidiplay is built with MIDIPLAY_WAVE_ONLY: it has no -w/-nl flags,
always writes `<input>.wav` next to the input and never loops, at 44100 Hz.
We symlink the song into the job's scratch dir and run there (MVP trick).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import RenderSpec, core_flag, symlinked_input

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


def bank_arg(variant: dict, paths) -> str:
    bank = variant.get("bank") or {}
    if bank.get("kind", "embedded") == "embedded":
        return str(int(bank["number"]))
    # external WOPL: path inside the image's bank dir
    return str(paths.banks / "wopl" / bank["file"])


def preflight(jobs, engines_json: dict) -> list[str]:
    """An unknown --emu-X flag is silently treated as a bank file by adlmidiplay (MVP footgun):
    every core flag these jobs need must appear in the binary's usage text."""
    usage = subprocess.run(["adlmidiplay", "--help"], capture_output=True, text=True).stdout
    problems: list[str] = []
    for j in jobs:
        flag = core_flag("adlmidi", CORE_FLAGS, j.core, engines_json)
        msg = f"adlmidiplay does not know {flag} (an unknown flag is silently treated as a bank file — MVP footgun)"
        if flag not in usage and msg not in problems:
            problems.append(msg)
    return problems


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["adlmidi"]
    link, out, pre = symlinked_input(job, tmpdir)
    argv = ["adlmidiplay", link.name, *eng["base_args"], core_flag("adlmidi", CORE_FLAGS, job.core, engines_json),
            bank_arg(job.variant, paths), str(eng["chips"])]
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, pre=[pre], native_rate=int(eng["native_rate"]))
