#!/usr/bin/env python3
"""Build ``catalog/variants.json`` and ``catalog/review.md``.

Inputs (all committed, all produced by the sibling tools):

* ``soundfonts.facets.json`` (``build.py``)   - one FluidSynth variant per canonical font
* ``soundfonts.json`` (``sf2scan.py``)        - INFO strings for ``source.sf2`` / legal notes
* ``adl_banks.json`` (``adlbanks.py``)        - 79 embedded libADLMIDI banks -> ``adl-b{NN}``
* ``adl_passes.json`` (hand-written)          - OPL2 / ESFMu / CQM passes -> ``adl-b{NN}-{suffix}``
* ``opn_banks.json`` (placeholder, M2b)       - listed in review.md only while ``enabled`` is false
* ``render/engines.json``                     - base flags of the render command templates

Variant ids are the cache/manifest identity (plan §6) and never change on re-render:
``sf2-<sha256[:10]>`` for fonts (byte-identical twins share the id and become alias
entries), ``adl-b{NN}`` for embedded banks, ``adl-b{NN}-opl2|-esfmu|-cqm`` for passes.

Canonical order (plan §8.4, used by ``sfr pack`` so common filters stay contiguous):
``engine -> chip -> completeness -> lineage -> size bucket -> name``, with the value
orders in ``ORDER`` below.  ``chip`` is inserted after ``engine`` so the OPL2/ESFM/CQM
passes do not interleave with the OPL3 renders inside the libADLMIDI block.  ``name``
is the bank number for embedded FM banks and the case-folded label (then file name)
for SoundFonts.  Ties are broken by id; the result is fully deterministic.

Usage::

    python3 -m catalog.variants [--catalog catalog] [--engines render/engines.json]
                                [--out catalog/variants.json] [--review catalog/review.md] [--quiet]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys

from . import adlbanks

SCHEMA = 1

ORDER = {
    "keys": ["engine", "chip", "completeness", "lineage", "size", "name", "id"],
    "engine": ["adlmidi", "opnmidi", "edmidi", "timidity", "sc55", "munt", "fluidsynth"],
    "chip": ["opl3", "opl2", "esfm", "cqm", "opn2", "opna", "opll", "psg", "scc", "gus", "pcm_rom", "la", "sf2"],
    "completeness": ["full_gm", "melodic_only", "partial", "drums_only", "single_instrument"],
    "lineage": ["roland", "yamaha_xg", "creative", "gravis", "gs_compat", "console", "fm_sampled", "fm_bank", "generic"],
    "size": ["<2MB", "2-16MB", "16-100MB", "100MB-1GB", ">1GB"],
}

ADL_BANK_LICENSE_FLAG = {  # adlbanks.PROVENANCE license_hint -> facets license_flag vocabulary
    "mit": "free",
    "mit_derived": "free",
    "ail2_permissive": "free",
    "sbtimbre_royalty_free": "free",
    "gpl": "gpl",
    "game_release": "unknown",
    "proprietary": "unknown",
    "unknown": "unknown",
}
ADL_REVIEW_HINTS = ("game_release", "proprietary", "unknown")  # listed under the issue #301 section
QUALITY_TAGS = ("non_gm", "mt32", "miss_ins", "broken_drums")

DEFAULT_ADL_BASE_ARGS = ["-f32", "-vm", "0", "--gain", "2.0"]
DEFAULT_FLUID_BASE_ARGS = [
    "-ni", "-T", "wav", "-O", "float", "-r", "48000", "-g", "0.5", "-R", "0", "-C", "0",
    "-o", "synth.polyphony=256", "-o", "synth.cpu-cores=1",
]
DEFAULT_DYNAMIC_LOADING_ABOVE = 128 * 1024 * 1024

ISSUE_301_URL = "https://github.com/Wohlstand/libADLMIDI/issues/301"


# ------------------------------------------------------------------ helpers


def slugify(text: str) -> str:
    return adlbanks.slugify(text)


def _engine_args(engines: dict | None, eid: str, key: str, default):
    try:
        return engines["engines"][eid][key]
    except (KeyError, TypeError):
        return default


def adl_cmd(bank_number: int, core_flag: str, engines: dict | None) -> str:
    base = " ".join(_engine_args(engines, "adlmidi", "base_args", DEFAULT_ADL_BASE_ARGS))
    chips = _engine_args(engines, "adlmidi", "chips", 1)
    # WAVE_ONLY build: no -w / -nl; output lands at <song>.mid.wav at 44.1 kHz (render/engines.json)
    return f"adlmidiplay <song>.mid {base} {core_flag} {bank_number} {chips}"


def fluid_cmd(file: str, nbytes: int, engines: dict | None) -> str:
    base = list(_engine_args(engines, "fluidsynth", "base_args", DEFAULT_FLUID_BASE_ARGS))
    threshold = int(_engine_args(engines, "fluidsynth", "dynamic_sample_loading_above_bytes", DEFAULT_DYNAMIC_LOADING_ABOVE))
    if nbytes >= threshold:
        base += ["-o", "synth.dynamic-sample-loading=1"]
    return "fluidsynth -F raw.wav " + " ".join(base) + f" /fonts/{_shell_quote(file)} <song>.mid"


def _shell_quote(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z0-9._+-]+", name) else "'" + name.replace("'", "'\\''") + "'"


def sort_key(v: dict):
    def idx(name, value):
        table = ORDER[name]
        return table.index(value) if value in table else len(table)

    f = v["facets"]
    bank = v.get("bank") or {}
    if bank.get("kind") == "embedded":
        name = f"{int(bank['number']):03d}"
    else:
        name = (v.get("label") or "").casefold() + "\x00" + ((v.get("source") or {}).get("file") or "")
    return (
        idx("engine", v["engine"]),
        idx("chip", v["chip_family"]),
        idx("completeness", f.get("completeness")),
        idx("lineage", f.get("lineage")),
        idx("size", f.get("size")),
        name,
        v["id"],
    )


# ------------------------------------------------------------------ SF2 variants


def sf2_variants(facets_doc: dict, scan_doc: dict | None, engines: dict | None) -> tuple[list[dict], list[dict]]:
    """(variants, aliases): one variant per canonical font with publish decided by the facets file."""
    info_by_file = {}
    if scan_doc:
        info_by_file = {f["file"]: f for f in scan_doc.get("fonts", [])}
    variants: list[dict] = []
    aliases: list[dict] = []
    used_slugs: dict[str, str] = {}
    by_id: dict[str, dict] = {}

    for rec in facets_doc["fonts"]:
        vid = rec.get("variant_id")
        if not vid:
            continue  # --no-hash scan; cannot make a stable id
        if rec.get("dup_of"):
            aliases.append({
                "file": rec["file"],
                "label": rec["label"],
                "variant_id": rec["alias_of"] or vid,
                "canonical_file": rec["dup_of"],
                "sha256": rec["sha256"],
            })
            continue
        fx = rec["facets"]
        scan = info_by_file.get(rec["file"]) or {}
        info = scan.get("info") or {}
        slug = slugify(os.path.splitext(rec["file"])[0])
        if slug in used_slugs and used_slugs[slug] != vid:
            slug = f"{slug}-{rec['sha256'][:6]}"
        used_slugs[slug] = vid

        legal = None
        if fx["license_flag"] == "roland_copyright":
            legal = "ICOP/INAM names Roland (" + (info.get("ICOP") or info.get("INAM") or "").strip()[:80] + \
                    "): sample set derived from Roland ROMs; published by default (decision D2), flip publish in overrides.json to withdraw"
        elif rec.get("notes") and "publish" in (rec.get("overrides_applied") or []):
            legal = rec["notes"]

        v = {
            "id": vid,
            "slug": slug,
            "label": rec["label"],
            "engine": "fluidsynth",
            "core": "fluidsynth",
            "chip_family": "sf2",
            "type": "sampled",
            "facets": {
                "engine": "fluidsynth",
                "chip": "sf2",
                "type": "sampled",
                "completeness": fx["completeness"],
                "bank_map": fx["bank_map"],
                "size": fx["size_bucket"],
                "lineage": fx["lineage"],
                "decade": fx["decade"],
                "quality": [],
                "instrument": rec.get("instrument"),
            },
            "year": rec.get("year"),
            "year_confidence": fx.get("year_confidence"),
            "publish": bool(rec.get("publish")),
            "requires_rom": False,
            "bank": None,
            "source": {
                "file": rec["file"],
                "sha256": rec["sha256"],
                "bytes": rec["bytes"],
                "sf2": {
                    "ifil": scan.get("ifil"),
                    "INAM": info.get("INAM"),
                    "IENG": info.get("IENG"),
                    "IPRD": info.get("IPRD"),
                    "ICOP": info.get("ICOP"),
                    "ICRD": info.get("ICRD"),
                    "ISFT": info.get("ISFT"),
                    "melodic_bank0": rec.get("melodic_bank0"),
                    "has_drums": rec.get("has_drums"),
                    "preset_count": rec.get("preset_count"),
                    "bank_count": len(rec.get("banks") or []),
                },
                "url": None,
                "license_flag": fx["license_flag"],
                "lineage_source": (rec.get("sources") or {}).get("lineage"),
            },
            "render": {"cmd": fluid_cmd(rec["file"], rec["bytes"], engines), "core": "fluidsynth"},
            "legal_note": legal,
            "aliases": [],
        }
        variants.append(v)
        by_id[vid] = v

    for a in aliases:
        canon = by_id.get(a["variant_id"])
        if canon is not None:
            canon["aliases"].append({"file": a["file"], "label": a["label"]})
    return variants, aliases


# ------------------------------------------------------------------ ADL variants


def adl_facets(bank: dict) -> dict:
    tags = bank["tags"]
    if tags.get("melodic_only"):
        comp, bmap = "melodic_only", "melodic_only"
    elif tags.get("non_gm"):
        comp, bmap = "partial", "non_gm"
    elif tags.get("miss_ins"):
        comp, bmap = "partial", "gm"  # GM-mapped but only a few instruments are defined
    else:
        comp, bmap = "full_gm", "gm"
    year = (bank.get("provenance") or {}).get("year")
    return {
        "completeness": comp,
        "bank_map": bmap,
        "size": "<2MB",
        "lineage": "fm_bank",
        "decade": f"{year // 10 * 10}s" if year else "unknown",
        "quality": [t for t in QUALITY_TAGS if tags.get(t)],
        "instrument": None,
    }


ADL_OVERRIDABLE = ("publish", "label", "notes", "completeness", "decade")


def load_adl_overrides(overrides_doc: dict | None) -> dict[int, dict]:
    """``overrides.json`` may carry ``"adl_banks": {"<index>": {publish, label, notes, completeness, decade}}``.

    (``build.py`` ignores that key; it is read only here.)  Returns ``{index: entry}``.
    """
    out: dict[int, dict] = {}
    for key, entry in ((overrides_doc or {}).get("adl_banks") or {}).items():
        if str(key).startswith("_"):
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"overrides.json adl_banks[{key!r}] must be an object")
        unknown = set(entry) - set(ADL_OVERRIDABLE) - {"_comment"}
        if unknown:
            raise ValueError(f"overrides.json adl_banks[{key!r}] has unknown keys {sorted(unknown)}")
        out[int(key)] = {k: v for k, v in entry.items() if k != "_comment"}
    return out


def adl_variant(bank: dict, engines: dict | None, *, suffix: str = "", core: str = "nuked", core_flag: str = "--emu-nuked",
                chip: str = "opl3", label_suffix: str | None = None, pass_id: str | None = None,
                override: dict | None = None) -> dict:
    n = bank["index"]
    ov = override or {}
    prov = bank.get("provenance") or {}
    hint = prov.get("license_hint", "unknown")
    fx = adl_facets(bank)
    if "completeness" in ov:
        fx["completeness"] = ov["completeness"]
    if "decade" in ov:
        fx["decade"] = ov["decade"]
    year = prov.get("year")
    label = ov.get("label") or bank.get("label") or adlbanks.label_for(bank)
    if label_suffix:
        label = f"{label} [{label_suffix}]"
    legal = None
    if hint in ADL_REVIEW_HINTS:
        legal = f"libADLMIDI embedded bank, license_hint={hint}: {prov.get('note')} (see {ISSUE_301_URL})"
    if ov.get("notes"):
        legal = (legal + " | " if legal else "") + str(ov["notes"])
    return {
        "id": f"adl-b{n}{suffix}",
        "slug": bank["slug"] + suffix,
        "label": label,
        "engine": "adlmidi",
        "core": core,
        "chip_family": chip,
        "type": "fm",
        "facets": {"engine": "adlmidi", "chip": chip, "type": "fm", **fx},
        "year": year,
        "year_confidence": ("high" if "decade" in ov else "low") if (year or "decade" in ov) else "unknown",
        "publish": bool(ov.get("publish", True)),
        "requires_rom": False,
        "bank": {
            "kind": "embedded",
            "number": n,
            "family": bank["family"],
            "name": bank["name"],
            "tags": dict(bank["tags"]),
            "pass": pass_id,
        },
        "source": {
            "file": None,
            "sha256": None,
            "bytes": None,
            "sf2": None,
            "url": prov.get("source") or adlbanks.LIST_OF_BANKS_URL,
            "license_flag": ADL_BANK_LICENSE_FLAG.get(hint, "unknown"),
            "license_hint": hint,
            "provenance": prov.get("note"),
        },
        "render": {"cmd": adl_cmd(n, core_flag, engines), "core": core},
        "legal_note": legal,
        "aliases": [],
    }


def adl_variants(banks_doc: dict, passes_doc: dict | None, engines: dict | None,
                 adl_overrides: dict[int, dict] | None = None) -> tuple[list[dict], dict]:
    by_index = {b["index"]: b for b in banks_doc["banks"]}
    ov = adl_overrides or {}
    unknown = sorted(set(ov) - set(by_index))
    if unknown:
        raise ValueError(f"overrides.json adl_banks names unknown bank indices {unknown}")
    out = [adl_variant(b, engines, override=ov.get(b["index"])) for b in banks_doc["banks"]]
    pass_counts: dict[str, int] = {}
    for p in (passes_doc or {}).get("passes", []):
        n = 0
        for idx in p["banks"]:
            if idx not in by_index:
                raise ValueError(f"adl_passes.json pass {p['id']} names bank {idx}, not in adl_banks.json")
            out.append(adl_variant(by_index[idx], engines, suffix=p["suffix"], core=p["core"], core_flag=p["core_flag"],
                                   chip=p["chip_family"], label_suffix=p.get("label_suffix"), pass_id=p["id"],
                                   override=ov.get(idx)))
            n += 1
        pass_counts[p["id"]] = n
    return out, pass_counts


# ------------------------------------------------------------------ assembly


def build(facets_doc: dict, scan_doc: dict | None, banks_doc: dict, passes_doc: dict | None,
          opn_doc: dict | None = None, engines: dict | None = None, overrides_doc: dict | None = None,
          now: _dt.datetime | None = None) -> dict:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    sf2, aliases = sf2_variants(facets_doc, scan_doc, engines)
    adl, pass_counts = adl_variants(banks_doc, passes_doc, engines, load_adl_overrides(overrides_doc))
    variants = sorted(sf2 + adl, key=sort_key)

    ids = [v["id"] for v in variants]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate variant ids: {dup}")
    slugs = [v["slug"] for v in variants]
    if len(set(slugs)) != len(slugs):
        dup = sorted({s for s in slugs if slugs.count(s) > 1})
        raise ValueError(f"duplicate slugs: {dup}")

    facet_counts: dict[str, dict] = {}
    for v in variants:
        for k, val in v["facets"].items():
            vals = val if isinstance(val, list) else [val]
            for x in vals:
                if x is None:
                    continue
                facet_counts.setdefault(k, {})
                facet_counts[k][str(x)] = facet_counts[k].get(str(x), 0) + 1
    facet_counts = {k: dict(sorted(d.items())) for k, d in sorted(facet_counts.items())}

    by_engine = _count(v["engine"] for v in variants)
    by_chip = _count(v["chip_family"] for v in variants)
    counts = {
        "total": len(variants),
        "published": sum(1 for v in variants if v["publish"]),
        "unpublished": sum(1 for v in variants if not v["publish"]),
        "sf2": len(sf2),
        "sf2_aliases": len(aliases),
        "adl": sum(1 for v in adl if not (v["bank"] or {}).get("pass")),
        **{f"adl_{k}": n for k, n in pass_counts.items()},
        "by_engine": by_engine,
        "by_chip": by_chip,
    }

    pending = {
        "opnmidi": {
            "source": "catalog/opn_banks.json",
            "banks": (opn_doc or {}).get("count"),
            "distinct": (opn_doc or {}).get("distinct_sha256"),
            "enabled": (opn_doc or {}).get("enabled_count", 0),
            "ids": sorted(i for b in (opn_doc or {}).get("banks", []) for i in b.get("variant_ids", [])),
        },
        "edmidi": {"ids": ["edm-opll", "edm-psg", "edm-scc", "edm-all"]},
        "timidity": {"ids": ["gus-freepats"]},
        "sc55": {"ids": ["sc55-<romset>"], "requires_rom": True, "note": "one per romset found in roms/ at M2b (<= 9)"},
        "munt": {"ids": ["mt32", "cm32l"], "requires_rom": True},
        "adl_wopl": {"ids": ["adl-w-<sha256[:10]>"], "note": "extra full-GM .wopl from OPL3BankEditor with a license file (~5)"},
    }

    return {
        "schema": SCHEMA,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "_comment": [
            "Generated by python3 -m catalog.variants from soundfonts.facets.json + soundfonts.json + adl_banks.json + adl_passes.json (+ overrides.json 'adl_banks'); do not edit (use overrides.json / adl_passes.json).",
            "ids are the render/cache identity and never change: sf2-<sha256[:10]> (rename-proof, byte-identical twins share one id and appear in 'aliases'), adl-b{NN}, adl-b{NN}-opl2|-esfmu|-cqm.",
            "variants[] is in canonical order: " + " -> ".join(ORDER["keys"]) + " (value orders in 'order'); sfr pack cuts groups of 24 from this order, so keep it stable.",
            "name = bank number for embedded FM banks, case-folded label then file name for SoundFonts.",
            "facets.size is the facets-file size_bucket; facets.quality lists the bank flags (non_gm, mt32, miss_ins, broken_drums); bank.tags has the full boolean set incl. fourop/pseudo_fourop.",
            "render.cmd is a documentation template (<song> placeholder); sfr builds the real argv from render/engines.json.",
            "Engines not yet in the image (libOPNMIDI, libEDMIDI, TiMidity, Nuked-SC55, Munt) are listed under 'pending' and added at M2b.",
        ],
        "inputs": {
            "soundfonts_facets_generated_at": facets_doc.get("generated_at"),
            "soundfonts_scan_generated_at": (scan_doc or {}).get("generated_at"),
            "adl_banks_generated_at": banks_doc.get("generated_at"),
            "libadlmidi": banks_doc.get("libadlmidi"),
        },
        "order": ORDER,
        "counts": counts,
        "facets": facet_counts,
        "pending": pending,
        "aliases": aliases,
        "variants": variants,
    }


def _count(values) -> dict:
    c: dict = {}
    for v in values:
        c[v] = c.get(v, 0) + 1
    return dict(sorted(c.items()))


# ------------------------------------------------------------------ review.md


def _md_escape(s) -> str:
    if s is None:
        return ""
    return str(s).replace("|", "\\|").replace("\n", " ").strip()


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for r in rows:
        out.append("| " + " | ".join(_md_escape(c) for c in r) + " |")
    return "\n".join(out)


def review_markdown(doc: dict, facets_doc: dict, banks_doc: dict, opn_doc: dict | None) -> str:
    fonts = facets_doc["fonts"]
    by_file = {f["file"]: f for f in fonts}
    c = doc["counts"]
    L: list[str] = []
    L.append("# Catalog review")
    L.append("")
    L.append(f"Generated {doc['generated_at']} by `python3 -m catalog.variants` — do not edit. To change a decision edit "
             "`catalog/overrides.json` (fonts) or `catalog/adl_passes.json` (FM passes), then re-run "
             "`python3 -m catalog.build && python3 -m catalog.variants`.")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append(f"- {c['total']} variants in `variants.json` ({c['published']} published): {c['sf2']} SoundFont (FluidSynth) + "
             f"{c['adl']} libADLMIDI OPL3 + {c.get('adl_opl2', 0)} OPL2 pass + {c.get('adl_esfmu', 0)} ESFM + {c.get('adl_cqm', 0)} CQM; "
             f"{c['sf2_aliases']} byte-identical twins are aliases.")
    L.append(f"- Pending (M2b): libOPNMIDI {doc['pending']['opnmidi']['banks']} listed banks ({doc['pending']['opnmidi']['distinct']} distinct, "
             f"{doc['pending']['opnmidi']['enabled']} enabled), libEDMIDI 4, TiMidity 1, Nuked-SC55 ≤ 9, Munt 2.")
    unpublished = [v for v in doc["variants"] if not v["publish"]]
    L.append(f"- Unpublished variants: {len(unpublished)}" + ("" if not unpublished else " — " + ", ".join(v["id"] for v in unpublished)))
    L.append("")

    # ---- Roland
    roland = [f for f in fonts if f["facets"]["license_flag"] == "roland_copyright"]
    L.append(f"## Roland copyright fonts ({len(roland)})")
    L.append("")
    L.append("`license_flag = roland_copyright`: ICOP/ICMT/INAM mention Roland. These are (or contain) samples from Roland "
             "ROMs (SC-55/SC-88/JV/MT-32 families). Decision D2 publishes them by default; set `\"publish\": false` in "
             "`overrides.json` (key by sha256) to withdraw one. `variants.json` carries the same text in `legal_note`.")
    L.append("")
    scan_info = {}
    L.append(_table(["file", "variant", "label", "lineage", "match", "publish"],
                    [[f["file"], f["variant_id"], f["label"], f["facets"]["lineage"], f["sources"]["license_flag"], "yes" if f["publish"] else "NO"]
                     for f in roland]))
    L.append("")

    # ---- regex-only lineage
    regex_only = [f for f in fonts if (f["sources"]["lineage"] or "").startswith("regex:")]
    L.append(f"## Regex-only lineage ({len(regex_only)} fonts, low confidence)")
    L.append("")
    L.append("Lineage was decided by a regular-expression hit on an INFO string or the file name, not by a human. "
             "The matched field and text are shown; a wrong guess is fixed with `\"lineage\": \"...\"` in `overrides.json`. "
             f"Fonts with no hit are `generic` ({facets_doc['counts']['lineage'].get('generic', 0)}); "
             f"{sum(1 for f in fonts if f['sources']['lineage'] == 'override')} are pinned by override.")
    L.append("")
    for lin in [k for k in ORDER["lineage"] if k not in ("fm_bank", "generic")]:
        rows = [f for f in regex_only if f["facets"]["lineage"] == lin]
        if not rows:
            continue
        L.append(f"### {lin} ({len(rows)})")
        L.append("")
        L.append(_table(["file", "matched", "bank_map", "completeness"],
                        [[f["file"], f["sources"]["lineage"][len("regex:"):], f["facets"]["bank_map"], f["facets"]["completeness"]] for f in rows]))
        L.append("")

    # ---- years
    low = [f for f in fonts if f["facets"]["year_confidence"] == "low"]
    med = [f for f in fonts if f["facets"]["year_confidence"] == "medium"]
    yc = facets_doc["counts"]["year_confidence"]
    L.append(f"## Low-confidence years (low {len(low)}, medium {len(med)})")
    L.append("")
    L.append(f"`year_confidence`: high {yc.get('high', 0)} (4-digit year in ICRD) · medium {yc.get('medium', 0)} (2-digit ICRD date or a year "
             f"found in ICMT/INAM prose) · low {yc.get('low', 0)} (year taken from the file name) · unknown {yc.get('unknown', 0)}. "
             "Fix with `\"year\": 1997` in `overrides.json`.")
    L.append("")
    if low:
        L.append("### low")
        L.append("")
        L.append(_table(["file", "year", "source"], [[f["file"], f["year"], f["sources"]["year"]] for f in low]))
        L.append("")
    if med:
        L.append("### medium")
        L.append("")
        L.append(_table(["file", "year", "source"], [[f["file"], f["year"], f["sources"]["year"]] for f in med]))
        L.append("")

    # ---- duplicates
    dups = facets_doc.get("duplicates", [])
    L.append(f"## Byte-identical duplicates ({len(dups)} groups)")
    L.append("")
    L.append("Same sha256 → one variant; the twin is an alias (search finds both names, one render).")
    L.append("")
    L.append(_table(["variant", "canonical file", "twin(s)", "bytes"],
                    [[f"sf2-{d['sha256'][:10]}", d["canonical"], ", ".join(d["twins"]), by_file[d["canonical"]]["bytes"]] for d in dups]))
    L.append("")

    # ---- overrides in effect
    overridden = [f for f in fonts if f.get("overrides_applied")]
    L.append(f"## Overrides in effect ({len(overridden)} fonts)")
    L.append("")
    L.append(_table(["file", "keys", "notes"], [[f["file"], ", ".join(f["overrides_applied"]), f.get("notes")] for f in overridden]))
    L.append("")

    # ---- ADL banks / issue 301
    banks = banks_doc["banks"]
    hints = banks_doc.get("license_hint_counts", {})
    flagged = [b for b in banks if b["provenance"]["license_hint"] in ADL_REVIEW_HINTS]
    L.append(f"## libADLMIDI embedded banks and issue #301 ({len(flagged)} of {len(banks)} flagged)")
    L.append("")
    L.append(f"libADLMIDI issue #301 (<{ISSUE_301_URL}>, opened 2025-11 by the Debian packager, still open) questions the legal "
             "status of the binary banks in `fm_banks/`: several were ripped from shipped games and most have no license of their own, "
             "so the project's GPL-3 claim cannot cover them. `fm_banks/list-of-banks.txt` at the pinned commit documents each bank's origin; "
             "`adlbanks.py` transcribes that into `provenance.license_hint`. Counts: " +
             ", ".join(f"{k} {v}" for k, v in hints.items()) + ".")
    L.append("")
    L.append("Banks whose hint is `game_release`, `proprietary` or `unknown` are listed below; all are published by default "
             "(the site plays them, it does not redistribute the bank files). To withdraw one add "
             "`\"adl_banks\": {\"<index>\": {\"publish\": false, \"notes\": \"...\"}}` to `overrides.json` "
             "(that key is read only by `variants.py`; it also accepts `label`, `completeness`, `decade`).")
    L.append("")
    L.append(_table(["bank", "variant(s)", "label", "hint", "publish", "origin"],
                    [[b["index"], ", ".join(v["id"] for v in doc["variants"] if (v.get("bank") or {}).get("number") == b["index"]),
                      b["label"], b["provenance"]["license_hint"],
                      "yes" if all(v["publish"] for v in doc["variants"] if (v.get("bank") or {}).get("number") == b["index"]) else "NO",
                      b["provenance"]["note"]] for b in flagged]))
    L.append("")
    L.append("Quality flags from the bank list (`facets.quality`): " +
             ", ".join(f"{k} {banks_doc['tag_counts'].get(k, 0)}" for k in adlbanks.TAG_KEYS) + ". "
             "MT-32-style banks (`mt32`) are 128-instrument banks in MT-32 program order, so GM files pick wrong timbres; "
             "`non_gm` banks are classified `partial`, `miss_ins` banks `partial` (few instruments defined), `melodic_only` banks have no drums.")
    L.append("")

    # ---- OPN placeholder
    L.append("## libOPNMIDI banks (M2b placeholder)")
    L.append("")
    if opn_doc:
        L.append(f"{opn_doc['count']} listed in `opn_banks.json`, {opn_doc['distinct_sha256']} distinct files, "
                 f"{opn_doc['enabled_count']} enabled. Unknown-license entries need a decision before they enter the image:")
        L.append("")
        L.append(_table(["slug", "name", "license", "duplicate of", "note"],
                        [[b["slug"], b["name"], b["license"], b.get("duplicate_of") or "", b.get("license_note")] for b in opn_doc["banks"]]))
    else:
        L.append("`opn_banks.json` not available.")
    L.append("")

    # ---- ROM engines
    L.append("## ROM-engine variants (M2b placeholder)")
    L.append("")
    L.append("No ROM-engine variant exists yet. At M2b `sfr scan-roms` will add one `sc55-<romset>` per Nuked-SC55 ROM set found "
             "in `roms/` (≤ 9) and `mt32` / `cm32l` for Munt; each will be listed here with `requires_rom: true`, the ROM set "
             "name and sha256, and `publish` defaulting to true (decision D2). ROM files never enter the repo or the image.")
    L.append("")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ CLI


def _load(path: str, required: bool = True):
    if not os.path.exists(path):
        if required:
            raise FileNotFoundError(path)
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def write_json(doc: dict, out: str) -> None:
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--catalog", default="catalog", help="directory with the input json files (default: %(default)s)")
    ap.add_argument("--engines", default=os.path.join("render", "engines.json"))
    ap.add_argument("--out", default=None, help="default: <catalog>/variants.json")
    ap.add_argument("--review", default=None, help="default: <catalog>/review.md; 'none' to skip")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    log = (lambda *_: None) if args.quiet else (lambda *a: print(*a, file=sys.stderr))
    cat = args.catalog

    facets_doc = _load(os.path.join(cat, "soundfonts.facets.json"))
    scan_doc = _load(os.path.join(cat, "soundfonts.json"), required=False)
    banks_doc = _load(os.path.join(cat, "adl_banks.json"))
    passes_doc = _load(os.path.join(cat, "adl_passes.json"), required=False)
    opn_doc = _load(os.path.join(cat, "opn_banks.json"), required=False)
    overrides_doc = _load(os.path.join(cat, "overrides.json"), required=False)
    engines = _load(args.engines, required=False)
    if engines is None:
        log(f"warning: {args.engines} not found; using built-in default flags for render.cmd")

    doc = build(facets_doc, scan_doc, banks_doc, passes_doc, opn_doc, engines, overrides_doc)
    out = args.out or os.path.join(cat, "variants.json")
    write_json(doc, out)
    c = doc["counts"]
    log(f"wrote {out}: {c['total']} variants ({c['published']} published) = sf2 {c['sf2']} + adl {c['adl']} + "
        + " + ".join(f"{k[4:]} {v}" for k, v in c.items() if k.startswith("adl_")) + f"; aliases {c['sf2_aliases']}")
    log("  by_chip " + "  ".join(f"{k}={v}" for k, v in c["by_chip"].items()))

    review = args.review or os.path.join(cat, "review.md")
    if review != "none":
        md = review_markdown(doc, facets_doc, banks_doc, opn_doc)
        tmp = review + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(md)
        os.replace(tmp, review)
        log(f"wrote {review}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
