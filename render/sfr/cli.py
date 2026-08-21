"""Command-line entry point. Sub-commands are filled in milestone by milestone:

  scan-fonts | plan | render | pack | manifest | publish | status | retry-failed
"""
from __future__ import annotations

import argparse
import sys

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sfr", description="Soundfont Explorer render pipeline")
    p.add_argument("--version", action="version", version=f"sfr {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("doctor", help="print tool versions found in this environment")
    return p


def cmd_doctor() -> int:
    import shutil
    import subprocess

    tools = {
        "adlmidiplay": ["adlmidiplay", "--help"],
        "fluidsynth": ["fluidsynth", "--version"],
        "ffmpeg": ["ffmpeg", "-hide_banner", "-version"],
        "opusenc": ["opusenc", "--version"],
        "opusdec": ["opusdec", "--version"],
        "ffprobe": ["ffprobe", "-hide_banner", "-version"],
    }
    ok = True
    for name, argv in tools.items():
        path = shutil.which(argv[0])
        if not path:
            print(f"{name:12} MISSING")
            ok = False
            continue
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            first = (out.stdout or out.stderr).strip().splitlines()[:1]
            print(f"{name:12} {path}  {first[0] if first else ''}")
        except Exception as e:  # pragma: no cover - diagnostic only
            print(f"{name:12} {path}  (error: {e})")
            ok = False
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None:
        parser.print_help()
        return 0
    if args.cmd == "doctor":
        return cmd_doctor()
    print(f"unknown command {args.cmd}", file=sys.stderr)
    return 2
