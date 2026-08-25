"""Merge ``catalog/soundfonts.json`` with ``catalog/overrides.json`` into facets.

Writes ``catalog/soundfonts.facets.json`` (schema in ``catalog/README.md``):
one record per scanned font carrying the derived facet values, the duplicate
groups (byte-identical by sha256), and an aggregated ``counts`` section.

Facet rules (plan §6) are implemented by pure functions so the tests can pin
them down; everything that comes from a regex or a heuristic records *why*
(``*_source`` fields) so ``review.md`` can list regex-only decisions.

Usage::

    python3 -m catalog.build [--scan catalog/soundfonts.json]
                             [--overrides catalog/overrides.json]
                             [--out catalog/soundfonts.facets.json] [--quiet]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

from ._util import count_values, decade_of, now_iso, utc_now, write_json

SCHEMA = 1

# Completeness -------------------------------------------------------------------
FULL_GM_MIN_MELODIC = 100
SINGLE_INSTRUMENT_PREFIX = re.compile(r"^(drums|piano|guitar|bass)_", re.IGNORECASE)
COMPLETENESS_VALUES = ("full_gm", "melodic_only", "drums_only", "partial", "single_instrument")

# Bank map -----------------------------------------------------------------------
BANK_MAP_VALUES = ("gm", "gs_var", "xg", "gm2", "multi", "drums_only", "melodic_only")
GS_VARIATION_BANKS = set(range(1, 64)) | {126, 127}  # Roland variation range + SC-55 CM-64/MT-32 compat banks
GM2_BANKS = {120, 121}  # GM2 rhythm / melodic MSB
XG_NAME_RE = re.compile(r"(?<![a-z])xgd?(?![a-z])|s-?yxg|db50|(?<![a-z])mu-?\d{2,}", re.IGNORECASE)

# Size ---------------------------------------------------------------------------
MIB = 1 << 20
SIZE_BUCKETS = (  # label, upper bound (exclusive), binary units
    ("<2MB", 2 * MIB),
    ("2-16MB", 16 * MIB),
    ("16-100MB", 100 * MIB),
    ("100MB-1GB", 1024 * MIB),
    (">1GB", None),
)

# Lineage ------------------------------------------------------------------------
# Ordered, most specific first: within a field group the first lineage whose regex
# matches wins.  ``overrides.json`` ``lineage_regex`` replaces this table wholesale
# (same shape; JSON object order is the priority order).
# ``(?<![a-z0-9])`` / ``(?![a-z0-9])`` are "word boundaries" that also treat ``_``
# as a separator (``\b`` does not), so ``GM_GS_XG`` still matches ``gs``.
DEFAULT_LINEAGE_REGEX = {
    "console": (
        r"nintendo|snes|(?<![a-z0-9])n64(?![a-z0-9])|genesis|(?<![a-z])sega|gameboy|game boy|(?<![a-z0-9])gba(?![a-z0-9])"
        r"|(?<![a-z0-9])psx(?![a-z0-9])|playstation|arcade"
    ),
    "fm_sampled": r"(?<![a-z])opl[234]?(?![a-z])|(?<![a-z])fm[ _-]|adlib|yamaha fm|ym2\d|sb16|soundblaster.*fm",
    "gravis": r"gravis|ultrasound|(?<![a-z0-9])gus(?![a-z0-9])|freepats|eawpats",
    "roland": r"roland|sc-?55|sc-?88|sound ?canvas|jv-?\d|mt-?32|cm-?32|gs sound set|virtual sound canvas",
    "yamaha_xg": r"yamaha|(?<![a-z0-9])xg(?![a-z0-9])|s-?yxg|db50|sw60|(?<![a-z])mu-?\d{2,}",
    "creative": (
        r"creative|sound ?blaster|awe ?32|awe ?64|live!|audigy|(?<![a-z])e-?mu(?![a-z])|emu ?8000"
        r"|(?<![a-z0-9])\d+mb?gmgs|(?<![a-z])ct\d+mgm|ctgmgs"
    ),
    "gs_compat": r"(?<![a-z0-9])gs(?![a-z0-9])|general standard|gmgs",
}
LINEAGE_VALUES = tuple(DEFAULT_LINEAGE_REGEX) + ("generic",)
# Field groups are tried in this order: a hit in the font's own name/filename beats
# a hit in the engineer/product/copyright strings, which beats a mention buried in
# the comment prose (ICMT is long and often names the *target* card, not the source).
LINEAGE_FIELD_GROUPS = (("INAM", "file"), ("IENG", "IPRD", "ICOP"), ("ICMT",))
# Values that authoring tools write by default; they say nothing about provenance.
# (Vienna SoundFont Studio stamps IPRD="SBAWE32" on every font it saves.)
DEFAULT_IGNORE_VALUES = (
    "SBAWE32",
    "SBAWE32PnP",
    "SBAWE32 PnP",
    "SynthFont Viena",
    "Vienna Master",
    "Polyphone",
    "Polyphone Soundfonts",
)

# Year / decade ------------------------------------------------------------------
YEAR4_RE = re.compile(r"(?<!\d)(19[89]\d|20[0-2]\d)(?!\d)")
YEAR_MIN = 1990
YEAR_CONFIDENCE = ("high", "medium", "low", "unknown")

# License ------------------------------------------------------------------------
LICENSE_RULES = (  # ordered; first match wins; fields: ICOP, ICMT, INAM
    ("roland_copyright", re.compile(r"roland", re.IGNORECASE)),
    ("gpl", re.compile(r"(?<![a-z])l?gpl(?![a-z])|general public licen[cs]e", re.IGNORECASE)),
    ("public_domain", re.compile(r"public domain|(?<![a-z0-9])cc0(?![a-z0-9])", re.IGNORECASE)),
    ("free", re.compile(r"(?<![a-z])free(ware)?(?![a-z])|creative commons|(?<![a-z])cc[- ]by(?![a-z])", re.IGNORECASE)),
)
LICENSE_VALUES = tuple(k for k, _ in LICENSE_RULES) + ("unknown",)
LICENSE_FIELDS = ("ICOP", "ICMT", "INAM")

# Overrides ----------------------------------------------------------------------
OVERRIDABLE = (
    "lineage",
    "completeness",
    "bank_map",
    "instrument",
    "year",
    "decade",
    "license_flag",
    "publish",
    "label",
    "notes",
)


# ------------------------------------------------------------------ pure facet rules


def single_instrument_kind(file: str) -> str | None:
    """'drums' | 'piano' | 'guitar' | 'bass' when the filename prefix marks a single-instrument font."""
    m = SINGLE_INSTRUMENT_PREFIX.match(file)
    return m.group(1).lower() if m else None


def structural_completeness(melodic_bank0: int, has_drums: bool) -> str:
    """Completeness from the preset layout alone (ignores the filename)."""
    if melodic_bank0 >= FULL_GM_MIN_MELODIC and has_drums:
        return "full_gm"
    if melodic_bank0 >= FULL_GM_MIN_MELODIC:
        return "melodic_only"
    if has_drums and melodic_bank0 == 0:
        return "drums_only"
    return "partial"


def completeness(file: str, melodic_bank0: int, has_drums: bool) -> str:
    """Plan §6: the single-instrument filename prefix wins over the structural class."""
    if single_instrument_kind(file):
        return "single_instrument"
    return structural_completeness(melodic_bank0, has_drums)


def bank_map(banks: list[int], name_hint: str = "") -> str:
    banks_set = set(banks)
    melodic = banks_set - {128}
    extras = melodic - {0}
    if 128 in banks_set and 0 not in banks_set:
        return "drums_only"  # percussion (+ maybe SFX/compat kits in 64/126/127) but no GM melodic bank
    if 128 not in banks_set:
        return "melodic_only"
    if melodic == {0}:
        return "gm"
    if XG_NAME_RE.search(name_hint or ""):
        return "xg"
    if extras & GM2_BANKS:
        return "gm2"
    if extras <= GS_VARIATION_BANKS:
        return "gs_var"
    return "multi"


def size_bucket(nbytes: int) -> str:
    for label, upper in SIZE_BUCKETS:
        if upper is None or nbytes < upper:
            return label
    raise AssertionError("unreachable")


def compile_lineage_regex(table: dict) -> list[tuple[str, re.Pattern]]:
    return [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in table.items()]


def lineage(fields: dict, compiled: list[tuple[str, re.Pattern]], ignore_values=DEFAULT_IGNORE_VALUES) -> tuple[str, str | None]:
    """Return (lineage, "field:match"); see LINEAGE_FIELD_GROUPS for the priority rule."""
    ignored = {v.lower() for v in ignore_values}
    for group in LINEAGE_FIELD_GROUPS:
        for name, rx in compiled:
            for field in group:
                text = fields.get(field) or ""
                if text.strip().lower() in ignored:
                    continue
                m = rx.search(text)
                if m:
                    return name, f"{field}:{m.group(0)}"
    return "generic", None


def _year_ok(y: int, now_year: int) -> bool:
    return YEAR_MIN <= y <= now_year


def year_from_icrd(text: str, now_year: int) -> int | None:
    """ICRD is free-form ("Aug 16, 1998", "98. 12. 25", "3/7/19", "2003-2021").

    A 4-digit year wins (latest one in range).  Otherwise 2-digit date tokens:
    a token > 31 cannot be a day/month so it is the year; else the last token.
    """
    years = [int(y) for y in YEAR4_RE.findall(text)]
    years = [y for y in years if _year_ok(y, now_year)]
    if years:
        return max(years)
    tokens = re.findall(r"(?<!\d)\d{1,2}(?!\d)", text)
    if len(tokens) < 2 or not re.search(r"\d", text):
        return None
    two = [int(t) for t in tokens]
    cand = [t for t in two if t > 31] or [two[-1]]
    yy = cand[0]
    year = 1900 + yy if yy >= 90 else 2000 + yy
    return year if _year_ok(year, now_year) else None


def year_from_text(text: str, now_year: int) -> int | None:
    """Latest plausible 4-digit year in arbitrary text (ICMT / INAM / filename)."""
    years = [int(y) for y in YEAR4_RE.findall(text or "")]
    years = [y for y in years if _year_ok(y, now_year)]
    return max(years) if years else None


def year_and_confidence(info: dict, file: str, now_year: int) -> tuple[int | None, str, str | None]:
    """ICRD -> ICMT -> INAM -> filename; returns (year, confidence, source_field)."""
    icrd = info.get("ICRD") or ""
    if icrd.strip():
        y = year_from_icrd(icrd, now_year)
        if y is not None:
            conf = "high" if YEAR4_RE.search(icrd) else "medium"
            return y, conf, "ICRD"
    for field in ("ICMT", "INAM"):
        y = year_from_text(info.get(field) or "", now_year)
        if y is not None:
            return y, "medium", field
    y = year_from_text(file, now_year)
    if y is not None:
        return y, "low", "file"
    return None, "unknown", None


def license_flag(info: dict) -> tuple[str, str | None]:
    for name, rx in LICENSE_RULES:
        for field in LICENSE_FIELDS:
            m = rx.search(info.get(field) or "")
            if m:
                return name, f"{field}:{m.group(0)}"
    return "unknown", None


def variant_id(sha256: str | None) -> str | None:
    return f"sf2-{sha256[:10]}" if sha256 else None


def default_label(file: str) -> str:
    """Display label = the file name without extension.

    The collection's file names are what people know these fonts by ("SC-55 SoundFont.v1.2b
    [Trevor0402]"), whereas INAM is frequently a tool default ("User Bank" ×21, "GS sound set
    (16 bit)" ×4) or an older revision string. INAM stays available in source.sf2.INAM and is shown
    in the now-playing panel. An explicit `label` in overrides.json still wins.
    """
    stem = os.path.splitext(file)[0]
    return re.sub(r"\s+", " ", stem).strip() or file


# ------------------------------------------------------------------ overrides


def load_overrides(path: str | None) -> dict:
    if not path or not os.path.exists(path):
        return {"lineage_regex": None, "ignore_values": None, "fonts": {}}
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    fonts = {k: v for k, v in (doc.get("fonts") or {}).items() if not k.startswith("_")}
    for key, entry in fonts.items():
        if not isinstance(entry, dict):
            raise ValueError(f"overrides.json fonts[{key!r}] must be an object")
        unknown = set(entry) - set(OVERRIDABLE) - {"file", "_comment"}
        if unknown:
            raise ValueError(f"overrides.json fonts[{key!r}] has unknown keys {sorted(unknown)}")
    return {"lineage_regex": doc.get("lineage_regex"), "ignore_values": doc.get("ignore_values"), "fonts": fonts}


def overrides_for(fonts: dict, sha256: str | None, file: str) -> tuple[dict, list[str]]:
    """sha256-keyed entry first, then the filename-keyed entry on top (filename is more specific)."""
    merged: dict = {}
    used: list[str] = []
    for key in (sha256, file):
        if key and key in fonts:
            merged.update({k: v for k, v in fonts[key].items() if k not in ("file", "_comment")})
            used.append(key)
    return merged, used


# ------------------------------------------------------------------ build


def build(scan: dict, overrides: dict, now: _dt.datetime | None = None) -> dict:
    now = now or utc_now()
    now_year = now.year
    regex_table = overrides.get("lineage_regex") or DEFAULT_LINEAGE_REGEX
    compiled = compile_lineage_regex(regex_table)
    ignore_values = overrides.get("ignore_values") or DEFAULT_IGNORE_VALUES
    override_fonts = overrides.get("fonts") or {}
    used_override_keys: set[str] = set()

    fonts_in = sorted(scan["fonts"], key=lambda f: f["file"])

    # duplicate groups by sha256 (canonical = first file name in sorted order)
    by_sha: dict[str, list[str]] = {}
    for f in fonts_in:
        if f.get("sha256"):
            by_sha.setdefault(f["sha256"], []).append(f["file"])
    canonical_of = {sha: files[0] for sha, files in by_sha.items()}
    duplicates = [
        {"sha256": sha, "canonical": files[0], "twins": files[1:]}
        for sha, files in sorted(by_sha.items(), key=lambda kv: kv[1][0])
        if len(files) > 1
    ]

    out_fonts = []
    for f in fonts_in:
        info = f.get("info") or {}
        file = f["file"]
        sha = f.get("sha256")
        ov, used = overrides_for(override_fonts, sha, file)
        used_override_keys.update(used)

        inst = single_instrument_kind(file)
        comp_struct = structural_completeness(f["melodic_bank0"], f["has_drums"])
        comp = completeness(file, f["melodic_bank0"], f["has_drums"])
        name_hint = " ".join(x for x in (info.get("INAM"), file) if x)
        bmap = bank_map(f["banks"], name_hint)
        lin, lin_match = lineage({**info, "file": file}, compiled, ignore_values)
        year, conf, year_src = year_and_confidence(info, file, now_year)
        lic, lic_match = license_flag(info)
        year_overridden = "year" in ov or "decade" in ov

        rec = {
            "file": file,
            "sha256": sha,
            "bytes": f["bytes"],
            "variant_id": variant_id(sha),
            "label": ov.get("label", default_label(file)),
            "parse_ok": bool(f.get("parse_ok")),
            "facets": {
                "completeness": ov.get("completeness", comp),
                "bank_map": ov.get("bank_map", bmap),
                "size_bucket": size_bucket(f["bytes"]),
                "lineage": ov.get("lineage", lin),
                "decade": ov.get("decade", decade_of(ov.get("year", year))),
                "year_confidence": conf,
                "license_flag": ov.get("license_flag", lic),
            },
            "instrument": ov.get("instrument", inst),
            "completeness_structural": comp_struct,
            "year": ov.get("year", year),
            "melodic_bank0": f["melodic_bank0"],
            "has_drums": f["has_drums"],
            "banks": f["banks"],
            "preset_count": f["preset_count"],
            "publish": bool(ov.get("publish", True)) and bool(f.get("parse_ok")),
            "dup_of": None,
            "alias_of": None,
            "sources": {
                "lineage": "override" if "lineage" in ov else ("regex:" + lin_match if lin_match else "default"),
                "year": "override" if year_overridden else year_src,
                "license_flag": "override" if "license_flag" in ov else ("regex:" + lic_match if lic_match else "default"),
                "completeness": "override" if "completeness" in ov else ("filename" if inst else "structure"),
                "bank_map": "override" if "bank_map" in ov else "structure",
            },
            "overrides_applied": sorted(k for k in ov),
            "notes": ov.get("notes"),
        }
        if year_overridden:
            rec["facets"]["year_confidence"] = "high"
        if sha and canonical_of.get(sha) != file:
            rec["dup_of"] = canonical_of[sha]
            rec["alias_of"] = variant_id(sha)
        out_fonts.append(rec)

    facet_names = ("completeness", "bank_map", "size_bucket", "lineage", "decade", "year_confidence", "license_flag")
    counts = {name: count_values(rec["facets"][name] for rec in out_fonts) for name in facet_names}
    counts["instrument"] = count_values(rec["instrument"] for rec in out_fonts if rec["instrument"])
    counts["completeness_structural"] = count_values(rec["completeness_structural"] for rec in out_fonts)
    counts["publish"] = count_values("true" if rec["publish"] else "false" for rec in out_fonts)

    unused = sorted(k for k in override_fonts if k not in used_override_keys)
    return {
        "schema": SCHEMA,
        "generated_at": now_iso(now),
        "scan_generated_at": scan.get("generated_at"),
        "count": len(out_fonts),
        "canonical_count": len(out_fonts) - sum(len(d["twins"]) for d in duplicates),
        "parse_failures": sum(1 for r in out_fonts if not r["parse_ok"]),
        "lineage_regex": dict(regex_table),
        "ignore_values": list(ignore_values),
        "counts": counts,
        "duplicates": duplicates,
        "unused_override_keys": unused,
        "fonts": out_fonts,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scan", default="catalog/soundfonts.json")
    ap.add_argument("--overrides", default="catalog/overrides.json")
    ap.add_argument("--out", default="catalog/soundfonts.facets.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    log = (lambda *_: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))

    with open(args.scan, encoding="utf-8") as fh:
        scan = json.load(fh)
    if scan.get("schema") != SCHEMA:
        print(f"error: {args.scan} has schema {scan.get('schema')}, expected {SCHEMA}", file=sys.stderr)
        return 2
    overrides = load_overrides(args.overrides)
    doc = build(scan, overrides)
    write_json(doc, args.out)

    log(f"wrote {args.out}: {doc['count']} fonts, {doc['canonical_count']} canonical, {doc['parse_failures']} parse failures")
    for name, vals in doc["counts"].items():
        log(f"  {name:24s} " + "  ".join(f"{k}={v}" for k, v in vals.items()))
    for d in doc["duplicates"]:
        log(f"  dup {d['sha256'][:10]}  {d['canonical']}  <=  {', '.join(d['twins'])}")
    if doc["unused_override_keys"]:
        log(f"  WARNING unused override keys: {doc['unused_override_keys']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
