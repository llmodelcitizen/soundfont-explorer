"""TiMidity++ with the FreePats GUS patch set (debian packages timidity + freepats).

    timidity -c /etc/timidity/freepats.cfg -Ow2 -s 48000 -EFreverb=d -EFchorus=d -A 25 --preserve-silence \
             -o <tmp>/raw.wav /songs/<id>.mid

All flags come from engines.json base_args. Three of them are load-bearing:
`-c …freepats.cfg` (trixie's default cfg sources an absent file), `--preserve-silence`
(otherwise leading silence is dropped and start calibration is wrong) and `-A 25`
(the default -A 70 clips). Output is 24-bit (`-Ow2`; TiMidity has no float output) and
ends at the last release, shorter than D — sfr pads.
"""
from __future__ import annotations

from pathlib import Path

from . import RenderSpec

REQUIRED_FLAGS = ("--preserve-silence",)


def spec(job, paths, engines_json: dict, tmpdir: Path) -> RenderSpec:
    eng = engines_json["engines"]["timidity"]
    base = list(eng["base_args"])
    for flag in REQUIRED_FLAGS:
        if flag not in base:
            raise ValueError(f"timidity base_args must contain {flag} (start calibration depends on it)")
    if "-c" not in base:
        raise ValueError("timidity base_args must name a config with -c (trixie's default cfg is broken)")
    out = tmpdir / "raw.wav"
    argv = ["timidity", *base, "-o", str(out), str(job.midi_path)]
    return RenderSpec(argv=argv, cwd=tmpdir, out_wav=out, native_rate=int(eng["native_rate"]))
