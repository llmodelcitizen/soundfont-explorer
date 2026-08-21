"""Engine adapters. Each module exposes

    spec(job, paths, engines_json, tmpdir) -> RenderSpec

describing how to produce a raw WAV for (song, variant) and what resources it needs. Optional hooks:

    weight_units(job) -> int                     memory admission units (default 1)
    preflight(jobs, engines_json) -> list[str]   fatal startup problems for these jobs (`sfr render`)
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..sched import JobError

MEM_UNIT_BYTES = 256 * 1024 * 1024   # one memory admission unit (plan §4: 64 units)

ADAPTERS = {"adlmidi": "adl", "fluidsynth": "fluid", "opnmidi": "opn", "edmidi": "edm",
            "timidity": "timidity", "sc55": "sc55", "munt": "munt"}


@dataclass
class RenderSpec:
    argv: list[str]
    cwd: Path
    out_wav: Path                   # where the engine leaves its WAV
    weight: int = 1                 # memory admission units (MEM_UNIT_BYTES each)
    timeout_s: int = 900
    rlimit_as_bytes: int | None = None
    pre: list[Callable[[], None]] = field(default_factory=list)   # run in the worker before argv (symlinks etc.)
    native_rate: int = 48000
    # headerless output: (ffmpeg sample format, rate, channels); None = self-describing WAV
    raw_format: tuple[str, int, int] | None = None
    # kill the engine once out_wav exceeds this many bytes (we trim to D anyway)
    max_out_bytes: int | None = None


def get(engine: str):
    try:
        name = ADAPTERS[engine]
    except KeyError:
        raise KeyError(f"unknown engine {engine!r}") from None
    return importlib.import_module(f".{name}", __name__)


def weight_units(job) -> int:
    fn = getattr(get(job.engine), "weight_units", None)
    return fn(job) if fn else 1


def ffmpeg_input_args(spec: "RenderSpec") -> list[str]:
    """ffmpeg arguments that precede `-i <out_wav>` (raw PCM needs its format spelled out)."""
    if spec.raw_format:
        fmt, rate, ch = spec.raw_format
        return ["-f", fmt, "-ar", str(rate), "-ac", str(ch)]
    return []


# ---- helpers shared by several adapters ------------------------------------

def core_flag(engine: str, builtin: dict[str, str], core: str, engines_json: dict | None = None) -> str:
    """--emu-X flag for a libADLMIDI/libOPNMIDI core: the adapter's table, extended by engines.json
    `engines.<engine>.cores` when present."""
    table = dict(builtin)
    try:
        table.update((engines_json or {})["engines"][engine]["cores"])
    except (KeyError, TypeError):
        pass
    try:
        return table[core]
    except KeyError:
        raise ValueError(f"unknown {engine} core {core!r}; known: {sorted(table)}") from None


def symlinked_input(job, tmpdir: Path) -> tuple[Path, Path, Callable[[], None]]:
    """For WAVE_ONLY players that always write `<input>.wav` next to the input and have no -w:
    symlink the song into the job's scratch dir and run there. Returns (link, out_wav, pre)."""
    midi_src = job.midi_path
    link = tmpdir / midi_src.name

    def pre():
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(midi_src, link)

    return link, tmpdir / (midi_src.name + ".wav"), pre


def romset_of(variant: dict, engine: str) -> str:
    """`variant["romset"]` == directory name under roms/ (owner-supplied ROM engines)."""
    rs = variant.get("romset")
    if not rs or "/" in rs:
        raise ValueError(f"{engine} variant {variant.get('id')!r} needs romset=<directory under roms/>")
    return rs


def rom_dir_check(rom_dir: Path, hint: str) -> Callable[[], None]:
    """pre hook: the engine prints its own error when ROMs are missing, but a clear reason is better."""
    def pre():
        if not rom_dir.is_dir():
            raise JobError("missing-rom", f"{rom_dir} does not exist ({hint})")
    return pre
