"""Shared fakes for sfr tests (no docker, no engines)."""
from __future__ import annotations

import json
from pathlib import Path

from sfr.config import RenderSettings

SETTINGS = RenderSettings()

ENGINES_JSON = {
    "render": {},
    "engines": {
        "adlmidi": {"label": "libADLMIDI", "version": "1.6.2", "commit": "c462209", "native_rate": 44100,
                    "base_args": ["-f32", "-vm", "0", "--gain", "2.0"], "chips": 1,
                    "start_offset_s": 0.0, "drift_ppm": 0},
        "fluidsynth": {"label": "FluidSynth", "version": "2.4.4", "native_rate": 48000,
                       "base_args": ["-ni", "-T", "wav", "-O", "float", "-r", "48000", "-g", "0.5", "-R", "0", "-C", "0"],
                       "dynamic_sample_loading_above_bytes": 134217728, "start_offset_s": 0.0, "drift_ppm": 0},
        "opnmidi": {"label": "libOPNMIDI", "version": None},
    },
}


def song(id="freedoom-e1m1", D=188, classes=None, root: Path = Path("/songs")):
    return {"id": id, "duration_s": D, "sha256": "s" * 64, "title": id, "composer": "x",
            "sequencer": "y", "source_url": "u", "license": {"id": "BSD-3-Clause", "url": "", "notice_text": ""},
            "include_classes": classes or ["full_gm", "melodic_only", "partial"], "_dir": root}


def sf2_variant(n=1, bytes_=50 << 20, completeness="full_gm", instrument=None):
    import hashlib
    sha = hashlib.sha256(f"font-{n}".encode()).hexdigest()
    f = {"completeness": completeness, "lineage": "generic", "size": "16-100MB"}
    if instrument:
        f["instrument"] = instrument
    return {"id": f"sf2-{sha[:10]}", "engine": "fluidsynth", "chip": "sf2", "type": "sampled",
            "label": f"Font {n}", "slug": f"font-{n}", "publish": True, "facets": f,
            "source": {"file": f"Font{n}.sf2", "sha256": sha, "bytes": bytes_}, "render": {"core": "default"}}


def adl_variant(bank=58, core="nuked", suffix=""):
    return {"id": f"adl-b{bank}{suffix}", "engine": "adlmidi", "chip": "opl3", "type": "fm",
            "label": f"bank {bank}", "slug": f"b{bank}", "publish": True,
            "facets": {"completeness": "full_gm", "lineage": "fm_bank", "size": "<2MB"},
            "bank": {"kind": "embedded", "number": bank}, "source": {"file": None, "sha256": None, "bytes": None},
            "render": {"core": core}}


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))
