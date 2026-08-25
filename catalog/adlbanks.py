"""Parse ``adlmidiplay --list-banks`` into ``catalog/adl_banks.json``.

The 79 banks embedded in libADLMIDI are listed by the player as lines like::

        Banks: 0 = AIL (The Fat Man 2op set, default AIL)
               3 = HMI (Descent:: Int) :NON-GM:
              40 = AIL (Caesar 2) :p4op: :MISS-INS:
              54 = AIL (Master of Magic) :4op: orchestral drums
              56 = SB (3d Cyberpuck :: melodic only)

i.e. ``N = Family (Name[:: qualifier]) [:TAG:]* [trailer]``.  This module turns each
line into ``{index, family, name, tags{...}, slug, raw, label, provenance}`` and
writes the list (schema in ``catalog/README.md``).

The player only exists inside the render image (plan §1.1), so the default is::

    docker run --rm sfr-render adlmidiplay --list-banks

``--from-file`` parses a captured listing instead (``catalog/tests/fixtures/adlmidiplay-banks.txt`` is
the MVP's capture of the same 79 lines; the tests use it so they need no docker).

Usage::

    python3 -m catalog.adlbanks [--from-file catalog/tests/fixtures/adlmidiplay-banks.txt] [--image sfr-render]
                                [--out catalog/adl_banks.json] [--quiet]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import subprocess
import sys

from ._util import load_engines, now_iso, utc_now, write_json

SCHEMA = 1
EXPECTED_COUNT = 79  # libADLMIDI 1.6.2 (c462209); a different count means the image changed
DEFAULT_IMAGE = "sfr-render"
LEGACY_LISTING = os.path.join("legacy", "mvp", "data", "banks.txt")
# render/engines.json is the single source for the libADLMIDI version/commit built into the image.
LIBADLMIDI = {k: load_engines()["engines"]["adlmidi"][k] for k in ("version", "commit")}
LIBADLMIDI_COMMIT = LIBADLMIDI["commit"]
LIST_OF_BANKS_URL = f"https://github.com/Wohlstand/libADLMIDI/blob/{LIBADLMIDI_COMMIT}/fm_banks/list-of-banks.txt"

FAMILIES = ("AIL", "HMI", "DMX", "WOPL", "TMB", "SB", "OP3", "Bisqwit", "EA")

# ``:MARKER:`` text (case-insensitive, spaces/dashes normalized) -> tag key.
# ``:: qualifier`` inside the parentheses can also carry a tag ("melodic only", "4op").
TAG_MARKERS = {
    "non-gm": "non_gm",
    "mt-32": "mt32",
    "mt32": "mt32",
    "miss-ins": "miss_ins",
    "broken drums": "broken_drums",
    "melodic only": "melodic_only",
    "4op": "fourop",
    "p4op": "pseudo_fourop",  # libADLMIDI's "pseudo 4-op" (two 2-op voices per note)
}
TAG_KEYS = ("non_gm", "mt32", "miss_ins", "broken_drums", "melodic_only", "fourop", "pseudo_fourop")

LINE_RE = re.compile(r"^\s*(?:Banks:\s*)?(\d+)\s*=\s*(.+?)\s*$")
ENTRY_RE = re.compile(r"^(?P<family>[A-Za-z0-9]+)\s*\((?P<inner>.*)\)(?P<trailer>.*)$")
MARKER_RE = re.compile(r":([A-Za-z0-9][A-Za-z0-9 \-]*?):")

# Provenance, hand-transcribed from fm_banks/list-of-banks.txt at the pinned commit
# (see LIST_OF_BANKS_URL) and the LICENSE-*.txt files next to it, plus the release
# year of the game/product the bank is named after (used only for the ``decade``
# facet, confidence "low").  ``license_hint`` vocabulary:
#   mit                 - the bank itself is MIT (The Fat Man tone libraries, DMXOPL, FMSynth IBKs)
#   mit_derived         - a modified copy of an MIT bank (mostly Fat Man 2-op/4-op mods)
#   ail2_permissive     - derived from John Miles' AIL2 open-source release ("open-source freeware")
#   gpl                 - GPL-licensed source release (Bisqwit adlmidi, Apogee Sound System / Duke3D)
#   sbtimbre_royalty_free - IBK files shipped with SBTimbre ("no royalty requirement ... freely")
#   game_release        - bank data shipped with / ripped from a commercial game (issue #301 territory)
#   proprietary         - carries an explicit "all rights reserved" notice
#   unknown             - origin not documented (LoudMouth "MegaPatch" mods, Wallace, ...)
PROVENANCE: dict[int, tuple[str, int | None, str]] = {
    0: ("mit", 1993, "AIL default 2-op bank (star_control_3.opl): The Fat Man tone library, MIT; AIL2 release is permissive"),
    1: ("gpl", 2010, "Bisqwit's GM bank released with the original adlmidi project (GPLv3+)"),
    2: ("game_release", 1995, "Descent / Asterix & Obelix HMI bank pair; Fat Man + AdLib SDK instruments, shipped with the game"),
    3: ("game_release", 1995, "Descent 'Int' bank pair shipped with the game"),
    4: ("game_release", 1995, "Descent 'Ham' bank pair shipped with the game"),
    5: ("game_release", 1995, "Descent 'Rick' bank pair shipped with the game"),
    6: ("game_release", 1996, "Descent 2 bank (Fat Man + AdLib SDK instruments) shipped with the game"),
    7: ("mit_derived", 1996, "Normality: modified Fat Man 2-op bank"),
    8: ("game_release", 1996, "Shattered Steel bank shipped with the game"),
    9: ("ail2_permissive", 1994, "Theme Park: MT-32-style bank from AdLib SDK examples + AIL2 default bank"),
    10: ("unknown", 1995, "'MegaPatch' by LoudMouth Inc. (3D Table Sports); LoudMouth's terms are not documented"),
    11: ("unknown", 1994, "Aces of the Deep: mixture of MegaPatch, AdLib SDK and Creative sample-bank instruments"),
    12: ("unknown", 1994, "Earthsiege: deeply modified LoudMouth MegaPatch"),
    13: ("mit_derived", 1995, "Anvil of Dawn: modified Fat Man 2-op bank"),
    14: ("game_release", 1994, "doom2.op2: Paul Radek's DMX bank modified by Bobby Prince, shipped with Doom II"),
    15: ("game_release", 1994, "heretic.op2: Paul Radek's default DMX bank as shipped with Heretic"),
    16: ("game_release", 1993, "doom1.op2: Paul Radek's DMX bank modified by Bobby Prince, shipped with Doom"),
    17: ("mit_derived", 1995, "Discworld / Grandest Fleet: modified Fat Man 2-op bank"),
    18: ("mit_derived", 1995, "Warcraft II: deeply modified Fat Man 2-op bank"),
    19: ("ail2_permissive", 1993, "Syndicate: modified AIL2 MT-32-style example bank"),
    20: ("mit_derived", 1995, "Guilty / Orion Conspiracy / TNSFC: modified Fat Man bank"),
    21: ("ail2_permissive", 1995, "Magic Carpet 2: modified AIL2 MT-32-style example bank"),
    22: ("game_release", 1996, "Nemesis: The Wizardry Adventure bank shipped with the game (Eric Heberling)"),
    23: ("ail2_permissive", 1995, "Jagged Alliance: modified AIL2 MT-32-style example bank"),
    24: ("game_release", 1993, "When Two Worlds War: few-instrument bank shipped with the game"),
    25: ("game_release", 1991, "Bard's Tale Construction Set: few-instrument bank shipped with the game"),
    26: ("mit_derived", 1993, "Return to Zork: Fat Man 2-op bank turned MT-32 style"),
    27: ("ail2_permissive", 1997, "Theme Hospital: deeply modified AIL2 example bank"),
    28: ("unknown", 1993, "NHL PA: modified LoudMouth MegaPatch"),
    29: ("mit_derived", 1994, "Inherit the Earth: Fat Man 2-op bank turned MT-32 style"),
    30: ("mit_derived", 1994, "Inherit the Earth (second file): Fat Man 2-op bank turned MT-32 style"),
    31: ("mit_derived", 1994, "Little Big Adventure: modified Fat Man 4-op bank"),
    32: ("ail2_permissive", 1996, "Heroes of Might and Magic II: modified AIL2 MT-32-style example bank"),
    33: ("game_release", 1994, "Death Gate bank shipped with the game (Eric Heberling)"),
    34: ("mit_derived", 1993, "FIFA International Soccer: modified Fat Man 2-op bank"),
    35: ("mit_derived", None, "Starship Invasion: modified Fat Man 2-op bank"),
    36: ("mit_derived", 1994, "Super Street Fighter II: modified Fat Man 4-op bank"),
    37: ("game_release", 1994, "Lords of the Realm: few-instrument bank shipped with the game"),
    38: ("mit_derived", 1993, "SimFarm / SimHealth: modified Fat Man 4-op bank"),
    39: ("mit_derived", 1993, "SimFarm / The Settlers / Serf City: modified Fat Man 2-op bank"),
    40: ("game_release", 1995, "Caesar II: few-instrument bank shipped with the game"),
    41: ("ail2_permissive", 1996, "Syndicate Wars: modified AIL2 MT-32-style example bank"),
    42: ("unknown", None, "LoudMouth MegaPatch modified by Probe Entertainment (Bubble Bobble)"),
    43: ("game_release", 1994, "Warcraft: Orcs & Humans bank shipped with the game"),
    44: ("unknown", 1996, "Terra Nova: Strike Force Centauri; not described in list-of-banks.txt"),
    45: ("mit_derived", 1994, "System Shock: modified Fat Man 4-op bank"),
    46: ("game_release", 1995, "Advanced Civilization bank shipped with the game (Eric Heberling)"),
    47: ("game_release", 1992, "Battle Chess 4000: 4-op MT-32-style bank shipped with the game"),
    48: ("game_release", 1995, "Ultimate Soccer Manager: 'messy' bank from the game"),
    49: ("ail2_permissive", 1992, "Air Bucks / The Blue and the Gray: modified AIL2 MT-32-style example bank"),
    50: ("mit_derived", 1993, "Ultima Underworld II: Fat Man 2-op bank turned MT-32 style"),
    51: ("mit_derived", 1993, "Kasparov's Gambit: Fat Man 2-op bank turned MT-32 style"),
    52: ("ail2_permissive", 1995, "High Seas Trader: modified AIL2 MT-32-style example bank (few instruments)"),
    53: ("mit_derived", 1994, "Master of Magic: modified Fat Man 4-op bank"),
    54: ("mit_derived", 1994, "Master of Magic (orchestral drums): modified Fat Man 4-op bank"),
    55: ("sbtimbre_royalty_free", 1995, "Action Soccer: assembled from SBTimbre IBK files"),
    56: ("mit", 1995, "3D Cyberpuck: minor mod of FMSynth's internal melodic IBK (MIT)"),
    57: ("sbtimbre_royalty_free", 1993, "Simon the Sorcerer: slightly modified SBTimbre mt32.ibk"),
    58: ("mit", 1995, "fat2.wopl: The Fat Man 2-op tone library (Win9x drivers), MIT"),
    59: ("mit", 1993, "fat4-fixed.wopl: The Fat Man 4-op tone library, MIT"),
    60: ("proprietary", 1994, "Junglevision 2-op set: '(c) 1994 Junglevision software. All rights reserved', reverse-engineered from a demo"),
    61: ("unknown", 1994, "Rob Wallace (Wallace Music & Sound) 2-op set, Nitemare 3D; no license documented"),
    62: ("gpl", 1996, "d3dtimbr.tmb: Duke Nukem 3D primary bank, Apogee Sound System GPLv2 source release"),
    63: ("gpl", 1997, "swtimbr.tmb: Shadow Warrior modification of the Duke Nukem 3D bank"),
    64: ("game_release", 1994, "raptor.op2: Paul Radek's DMX bank modified by Scott Host for Raptor"),
    65: ("sbtimbre_royalty_free", 2017, "gmopl_wohl_mod.ibk: SBTimbre gmopl.ibk modified by Wohlstand (+ his drum bank)"),
    66: ("mit", 1993, "JOconnel.IBK: Jamie O'Connell's bank, MIT with the FMSynth source release"),
    67: ("mit", 1994, "default.tmb: Apogee Sound System default (GENMIDI/PERCUS.IBK) = slightly damaged Fat Man copy, MIT"),
    68: ("gpl", 2017, "4-op GM bank by J.A. Nguyen and Wohlstand for the GPL Windows OPL3 driver"),
    69: ("game_release", 1997, "bloodtmb.tmb: modified LoudMouth bank shipped with Blood"),
    70: ("gpl", 1994, "rott.tmb: LEE.IBK/LEEDRUMS.IBK from the Rise of the Triad GPLv2+ source release"),
    71: ("gpl", 1998, "nam.tmb: minor modification of the Duke Nukem 3D bank"),
    72: ("mit", 2017, "DMXOPL3-by-sneakernets-GS.wopl: DMXOPL3 by Sneakernets (Shannon Freeman), MIT"),
    73: ("game_release", 1989, "Cartooners (EA, 1989): reverse-engineered bank data; samples mostly from AdLib Inc.'s free sample files"),
    74: ("sbtimbre_royalty_free", 1990, "Apogee-IMF-90.wopl: IMF-era instruments captured by Wohlstand, all from SBTimbre mt32.ibk"),
    75: ("game_release", 1993, "The Lost Vikings: 2-op/4-op bank shipped with the game"),
    76: ("game_release", 1996, "strife.op2: modified Paul Radek DMX bank shipped with Strife"),
    77: ("unknown", 1992, "msadlib.wopl: Creative's example bank from the Windows 3.1 AdLib/SB drivers"),
    78: ("game_release", 1992, "MonopolyDeluxe.ad: bank shipped with Monopoly Deluxe"),
}
LICENSE_HINTS = ("mit", "mit_derived", "ail2_permissive", "gpl", "sbtimbre_royalty_free", "game_release", "proprietary", "unknown")

# Friendlier display names where the listing text is cryptic.  Everything else is
# ``"{family} ({name})"``.
LABEL_OVERRIDES = {
    0: "AIL default (The Fat Man 2-op)",
    1: "Bisqwit (adlmidi 4-op/2-op mix)",
    14: "DMX Bobby Prince v2 (Doom II)",
    15: "DMX default (Heretic, Hexen)",
    16: "DMX Bobby Prince v1 (Doom)",
    58: "The Fat Man 2-op (Win9x OPL3 driver)",
    59: "The Fat Man 4-op",
    64: "DMX Scott Host (Raptor)",
    65: "GMOPL modded by Wohlstand (SB IBK)",
    66: "Jamie O'Connell's FM Synth bank",
    67: "Apogee Sound System default (TMB)",
    72: "DMXOPL3 by Sneakernets",
    74: "Apogee IMF 90-ish (WOPL)",
    76: "DMX (Strife)",
    77: "MS AdLib driver (Windows 3.x)",
}


class ParseError(ValueError):
    pass


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "bank"


def _norm_marker(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def parse_line(line: str) -> dict | None:
    """One listing line -> bank dict, or None for lines that are not bank entries."""
    m = LINE_RE.match(line)
    if not m:
        return None
    index = int(m.group(1))
    rest = m.group(2)
    em = ENTRY_RE.match(rest)
    if not em:
        raise ParseError(f"bank {index}: cannot parse {rest!r}")
    family = em.group("family")
    inner = em.group("inner")
    trailer = em.group("trailer")

    tags = {k: False for k in TAG_KEYS}
    unknown: list[str] = []

    def take_markers(text: str) -> str:
        def repl(mm: re.Match) -> str:
            key = TAG_MARKERS.get(_norm_marker(mm.group(1)))
            if key:
                tags[key] = True
            else:
                unknown.append(mm.group(1))
            return " "

        return MARKER_RE.sub(repl, text)

    inner = take_markers(inner)
    trailer = take_markers(trailer)

    # "Descent:: Int", "3d Cyberpuck :: melodic only", "TNSFC ::4op"
    qualifier = None
    if "::" in inner:
        head, _, tail = inner.partition("::")
        inner = head
        tail = tail.strip()
        key = TAG_MARKERS.get(_norm_marker(tail))
        if key:
            tags[key] = True
        elif tail:
            qualifier = tail

    name = re.sub(r"\s+", " ", inner).strip(" ,;")
    if qualifier:
        name = f"{name}: {qualifier}"
    trailer = re.sub(r"\s+", " ", trailer).strip(" ,;")
    if trailer:
        name = f"{name}, {trailer}"

    return {
        "index": index,
        "family": family,
        "name": name,
        "tags": tags,
        "slug": f"{index}-{slugify(family)}-{slugify(name)}",
        "raw": line.strip(),
        "unknown_markers": unknown,
    }


def parse_listing(text: str) -> list[dict]:
    banks = []
    for line in text.splitlines():
        rec = parse_line(line)
        if rec is not None:
            banks.append(rec)
    seen = set()
    for b in banks:
        if b["index"] in seen:
            raise ParseError(f"duplicate bank index {b['index']}")
        seen.add(b["index"])
    if banks != sorted(banks, key=lambda b: b["index"]):
        raise ParseError("bank indices out of order")
    return banks


def label_for(bank: dict) -> str:
    return LABEL_OVERRIDES.get(bank["index"], f"{bank['family']} ({bank['name']})")


def enrich(bank: dict) -> dict:
    """Add label + provenance (pure; keeps the parsed fields intact)."""
    hint, year, note = PROVENANCE.get(bank["index"], ("unknown", None, "not described in list-of-banks.txt"))
    out = dict(bank)
    out["label"] = label_for(bank)
    out["provenance"] = {
        "license_hint": hint,
        "year": year,
        "note": note,
        "source": LIST_OF_BANKS_URL,
    }
    return out


def build_doc(banks: list[dict], source: dict, now: _dt.datetime | None = None) -> dict:
    now = now or utc_now()
    banks = [enrich(b) for b in banks]
    families = sorted({b["family"] for b in banks})
    unknown_fam = [f for f in families if f not in FAMILIES]
    tag_counts = {k: sum(1 for b in banks if b["tags"][k]) for k in TAG_KEYS}
    lic_counts = {}
    for b in banks:
        h = b["provenance"]["license_hint"]
        lic_counts[h] = lic_counts.get(h, 0) + 1
    return {
        "schema": SCHEMA,
        "generated_at": now_iso(now),
        "source": source,
        "libadlmidi": dict(LIBADLMIDI),
        "count": len(banks),
        "families": families,
        "unknown_families": unknown_fam,
        "tag_counts": tag_counts,
        "license_hint_counts": dict(sorted(lic_counts.items())),
        "unknown_markers": sorted({m for b in banks for m in b["unknown_markers"]}),
        "banks": banks,
    }


def run_list_banks(image: str = DEFAULT_IMAGE, timeout: int = 120) -> str:
    cmd = ["docker", "run", "--rm", image, "adlmidiplay", "--list-banks"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()[:500]}")
    return proc.stdout


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from-file", metavar="PATH", help=f"parse a captured listing (e.g. {LEGACY_LISTING}) instead of running docker")
    ap.add_argument("--image", default=DEFAULT_IMAGE, help="docker image that has adlmidiplay (default: %(default)s)")
    ap.add_argument("--out", default=os.path.join("catalog", "adl_banks.json"))
    ap.add_argument("--expect", type=int, default=EXPECTED_COUNT, help="fail unless exactly this many banks are found (0 = any)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    log = (lambda *_: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))

    if args.from_file:
        with open(args.from_file, encoding="utf-8") as fh:
            text = fh.read()
        source = {"kind": "file", "path": args.from_file.replace(os.sep, "/")}
    else:
        text = run_list_banks(args.image)
        source = {"kind": "docker", "image": args.image, "command": "adlmidiplay --list-banks"}

    banks = parse_listing(text)
    if args.expect and len(banks) != args.expect:
        print(f"error: parsed {len(banks)} banks, expected {args.expect}", file=sys.stderr)
        return 1
    doc = build_doc(banks, source)
    if doc["unknown_markers"]:
        print(f"error: unknown tag markers {doc['unknown_markers']}; extend TAG_MARKERS", file=sys.stderr)
        return 1
    if doc["unknown_families"]:
        log(f"warning: families not in the plan's list: {doc['unknown_families']}")
    write_json(doc, args.out)
    log(f"wrote {args.out}: {doc['count']} banks; tags " + "  ".join(f"{k}={v}" for k, v in doc["tag_counts"].items()))
    log("  license hints " + "  ".join(f"{k}={v}" for k, v in doc["license_hint_counts"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
