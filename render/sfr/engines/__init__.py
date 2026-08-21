"""Engine adapters. Each module exposes

    spec(job, paths, engines_json, tmpdir) -> RenderSpec

describing how to produce a raw WAV for (song, variant) and what resources it needs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class RenderSpec:
    argv: list[str]
    cwd: Path
    out_wav: Path                   # where the engine leaves its WAV
    weight: int = 1                 # memory admission units (256 MB each)
    timeout_s: int = 900
    rlimit_as_bytes: int | None = None
    pre: list[Callable[[], None]] = field(default_factory=list)   # run in the worker before argv (symlinks etc.)
    native_rate: int = 48000
    env: dict[str, str] = field(default_factory=dict)
    # headerless output: (ffmpeg sample format, rate, channels); None = self-describing WAV
    raw_format: tuple[str, int, int] | None = None
    # kill the engine once out_wav exceeds this many bytes (we trim to D anyway)
    max_out_bytes: int | None = None


def get(engine: str):
    if engine == "adlmidi":
        from . import adl as m
    elif engine == "fluidsynth":
        from . import fluid as m
    elif engine == "opnmidi":
        from . import opn as m
    elif engine == "edmidi":
        from . import edm as m
    elif engine == "timidity":
        from . import timidity as m
    elif engine == "sc55":
        from . import sc55 as m
    elif engine == "munt":
        from . import munt as m
    else:
        raise KeyError(f"unknown engine {engine!r}")
    return m


def ffmpeg_input_args(spec: "RenderSpec") -> list[str]:
    """ffmpeg arguments that precede `-i <out_wav>` (raw PCM needs its format spelled out)."""
    if spec.raw_format:
        fmt, rate, ch = spec.raw_format
        return ["-f", fmt, "-ar", str(rate), "-ac", str(ch)]
    return []
