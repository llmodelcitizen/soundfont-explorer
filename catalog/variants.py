"""Build ``catalog/variants.json`` and ``catalog/review.md``.

Inputs (all committed, all produced by the sibling tools):

* ``soundfonts.facets.json`` (``build.py``)   - one FluidSynth variant per canonical font
* ``soundfonts.json`` (``sf2scan.py``)        - INFO strings for ``source.sf2`` / legal notes
* ``adl_banks.json`` (``adlbanks.py``)        - 79 embedded libADLMIDI banks -> ``adl-b{NN}``
* ``adl_passes.json`` (hand-written)          - OPL2 / ESFMu / CQM passes -> ``adl-b{NN}-{suffix}``
* ``opn_banks.json`` (hand-edited)            - WOPN banks; each ``enabled`` one -> ``opn-<slug>`` + ``opn-<slug>-opna``
* ``render/engines.json``                     - base flags of the render command templates (+ sc55 romset families)
* built-in tables below (M2b)                 - ``edm-opll|scc|all``, ``gus-freepats``, ``sc55-<family>`` (x9), ``mt32``, ``cm32l``

Variant ids are the cache/manifest identity (plan §6) and never change on re-render:
``sf2-<sha256[:10]>`` for fonts (byte-identical twins share the id and become alias
entries), ``adl-b{NN}`` for embedded banks, ``adl-b{NN}-opl2|-esfmu|-cqm`` for passes,
``opn-<slug>[-opna]``, ``edm-<module>``, ``gus-freepats``, ``sc55-<family>``, ``mt32``, ``cm32l``.
ROM-engine variants carry ``requires_rom: true`` and ``romset`` (== the ``roms/`` sub-directory).

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
from ._util import count_values, decade_of, write_json

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


# Per-file provenance (catalog/collections.json, built by catalog/provenance.py from the source
# archives by name+size+crc32). A font with no entry gets no collection — attribution is never guessed.
HERE = os.path.dirname(os.path.abspath(__file__))


def load_collections() -> dict:
    path = os.path.join(HERE, "collections.json")
    if not os.path.exists(path):
        return {"collections": {}, "files": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def collection_for(sha256: str | None, coll: dict) -> dict | None:
    cid = coll.get("files", {}).get(sha256 or "")
    c = coll.get("collections", {}).get(cid) if cid else None
    if not c:
        return None
    return {k: c.get(k) for k in ("id", "title", "url", "torrent")}


def sf2_variants(facets_doc: dict, scan_doc: dict | None, engines: dict | None) -> tuple[list[dict], list[dict]]:
    """(variants, aliases): one variant per canonical font with publish decided by the facets file."""
    collections = load_collections()
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

        coll_entry = collection_for(rec.get("sha256"), collections)

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
                "url": (coll_entry or {}).get("url"),
                "collection": coll_entry,
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
        "decade": decade_of(year),
        "quality": [t for t in QUALITY_TAGS if tags.get(t)],
        "instrument": None,
    }


ADL_OVERRIDABLE = ("publish", "label", "notes", "completeness", "decade")
VARIANT_OVERRIDABLE = ("publish", "label", "notes")


def _load_override_section(overrides_doc: dict | None, section: str, allowed: tuple[str, ...], key_fn) -> dict:
    """``{key_fn(key): entry}`` for one top-level section of ``overrides.json``; ``_``-keys are skipped."""
    out: dict = {}
    for key, entry in ((overrides_doc or {}).get(section) or {}).items():
        if str(key).startswith("_"):
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"overrides.json {section}[{key!r}] must be an object")
        unknown = set(entry) - set(allowed) - {"_comment"}
        if unknown:
            raise ValueError(f"overrides.json {section}[{key!r}] has unknown keys {sorted(unknown)}")
        out[key_fn(key)] = {k: v for k, v in entry.items() if k != "_comment"}
    return out


def load_adl_overrides(overrides_doc: dict | None) -> dict[int, dict]:
    """``overrides.json`` may carry ``"adl_banks": {"<index>": {publish, label, notes, completeness, decade}}``.

    (``build.py`` ignores that key; it is read only here.)  Returns ``{index: entry}``.
    """
    return _load_override_section(overrides_doc, "adl_banks", ADL_OVERRIDABLE, int)


def load_variant_overrides(overrides_doc: dict | None) -> dict[str, dict]:
    """``overrides.json`` may carry ``"variants": {"<variant id>": {publish, label, notes}}``.

    Applies to ONE variant id (any engine) after assembly - e.g. a single ESFM pass that null-tests
    identical to its Nuked render, or one ROM-engine variant - unlike ``adl_banks`` which covers every
    pass of a bank.  (``build.py`` ignores that key; it is read only here.)  Returns ``{id: entry}``.
    """
    return _load_override_section(overrides_doc, "variants", VARIANT_OVERRIDABLE, str)


def apply_variant_overrides(variants: list[dict], overrides: dict[str, dict]) -> None:
    by_id = {v["id"]: v for v in variants}
    unknown = sorted(set(overrides) - set(by_id))
    if unknown:
        raise ValueError(f"overrides.json variants names unknown variant ids {unknown}")
    for vid, ov in overrides.items():
        v = by_id[vid]
        if "publish" in ov:
            v["publish"] = bool(ov["publish"])
        if ov.get("label"):
            v["label"] = str(ov["label"])
        if ov.get("notes"):
            v["legal_note"] = (v["legal_note"] + " | " if v.get("legal_note") else "") + str(ov["notes"])
        v["override"] = {k: ov[k] for k in VARIANT_OVERRIDABLE if k in ov}


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


# ------------------------------------------------------------------ M2b engines
# libOPNMIDI (OPN2/OPNA), libEDMIDI (OPLL/SCC), TiMidity++/FreePats (GUS), Nuked-SC55, Munt.
# Facet policy for these (plan §6): lineage fm_bank for FM, gravis for GUS, roland for SC-55/MT-32;
# decade = hardware era of the emulated chip/module (year = its introduction year, medium confidence);
# completeness full_gm unless documented otherwise (edm-scc has no percussion, FreePats 2006 defines 72
# melodic programs -> partial, the MT-32 family is pre-GM -> bank_map non_gm + quality mt32).

DEFAULT_OPN_BASE_ARGS = ["-f32", "-vm", "0", "--gain", "2.0"]
DEFAULT_EDM_BASE_ARGS = ["-r", "48000", "-n", "8", "-f", "f32"]
DEFAULT_TIMIDITY_BASE_ARGS = ["-c", "/etc/timidity/freepats.cfg", "-Ow2", "-s", "48000", "-EFreverb=d", "-EFchorus=d",
                              "-A", "25", "--preserve-silence"]
DEFAULT_SC55_BASE_ARGS = ["-f", "f32", "--end", "release", "-n", "1"]
DEFAULT_MUNT_BASE_ARGS = ["--quiet", "-f", "--src-quality", "3", "--renderer-type", "1", "--output-sample-format", "1",
                          "--record-max-start-silence", "-1", "--record-max-end-silence", "-1"]

OPN_PASSES = (  # (id suffix, slug suffix, label suffix, core, core flag, chip, year, chip name)
    ("", "-opn2", None, "nuked-3438", "--emu-nuked-3438", "opn2", 1988, "YM2612 OPN2 (Nuked-OPN2 core)"),
    ("-opna", "-opna", "OPNA", "mame-opna", "--emu-mame-opna", "opna", 1985, "YM2608 OPNA (MAME core)"),
)
OPN_LICENSE_FLAG = {"mit": "free", "bsd-3-clause": "free", "bsd": "free", "gpl": "gpl", "public domain": "public_domain",
                    "cc0": "public_domain", "unknown": "unknown"}
OPN_BANKS_DIR = "/opt/banks/wopn"

EDM_MODULES = (  # id, module, label, chip_family, facets.chip, core, completeness, bank_map, year, note
    ("edm-opll", "opll", "Emu De MIDI OPLL (YM2413)", "opll", "opll", "emu2413", "full_gm", "gm", 1986,
     "YM2413 OPLL: 15 fixed FM instruments + rhythm section, GM programs mapped by Emu De MIDI"),
    ("edm-scc", "scc", "Emu De MIDI SCC (Konami SCC wavetable)", "scc", "scc", "emu2212", "melodic_only", "melodic_only", 1987,
     "SCC modules only: melodic voices, no percussion (channel 10 goes to the OPLL rhythm section, which is silenced here)"),
    ("edm-all", "all", "Emu De MIDI OPLL + SCC (layered)", "opll", ["opll", "scc"], "emu2413+emu2212", "full_gm", "gm", 1987,
     "libEDMIDI's own wiring: every melodic note on one OPLL and one SCC module, drums on the OPLL rhythm section"),
)

SC55_FAMILIES = (  # family flag, label, year (None = not pinned down), note
    ("mk2", "Roland SC-55mk2", 1993, "Sound Canvas SC-55mk2 (GS); -r gs applied automatically by nuked-sc55-render"),
    ("st", "Roland SC-55ST", None, "SC-55ST (mk2 ROM family)"),
    ("mk1", "Roland SC-55", 1991, "original Sound Canvas SC-55 (mk1)"),
    ("cm300", "Roland CM-300 / SCC-1", 1991, "CM-300 module / SCC-1 ISA card (SC-55 mk1 engine)"),
    ("jv880", "Roland JV-880", 1992, "JV-880 rack synth (JV-80 engine, not a GS module)"),
    ("scb55", "Roland SCB-55", None, "SCB-55 wave-blaster daughterboard (SC-55mk2 engine)"),
    ("rlp3237", "Roland RLP-3237", None, "RLP-3237 daughterboard (SC-55 family)"),
    ("sc155", "Roland SC-155", 1992, "SC-155 (SC-55 mk1 engine with faders)"),
    ("sc155mk2", "Roland SC-155mk2", 1993, "SC-155mk2 (SC-55mk2 engine)"),
)
SC55_NONCOMMERCIAL = ("Nuked-SC55 (jcmoyer fork) is under a MAME-style non-commercial licence (redistributions may not be "
                      "sold or used in a commercial product) and the render uses owner-supplied Roland ROM images; "
                      "published by default (decision D2), the site is non-commercial")

MUNT_MODELS = (  # id, model (-i), romset dir, label, year, note
    ("mt32", "mt32", "mt32", "Roland MT-32", 1987, "LA synthesis, pre-GM 128-instrument map (GM program numbers pick MT-32 timbres)"),
    ("cm32l", "cm32l", "cm32l", "Roland CM-32L", 1989, "MT-32 successor with 33 extra sound effects; same pre-GM instrument map"),
)
MUNT_LEGAL = ("Munt renders with owner-supplied Roland control + PCM ROM images (roms/<romset>/, never in the repo or image); "
              "published by default (decision D2), flip publish in overrides.json to withdraw")


def _hw_source(url: str | None, license_flag: str, **extra) -> dict:
    return {"file": None, "sha256": None, "bytes": None, "sf2": None, "url": url, "license_flag": license_flag,
            "decade_basis": "hardware era of the emulated chip/module", **extra}


def opn_cmd(bank_file: str, core_flag: str, engines: dict | None) -> str:
    base = " ".join(_engine_args(engines, "opnmidi", "base_args", DEFAULT_OPN_BASE_ARGS))
    chips = _engine_args(engines, "opnmidi", "chips", 2)
    flag = _engine_args(engines, "opnmidi", "chips_flag", "--chips")
    # WAVE_ONLY build: options first, then the bank (positional, no -b), then the MIDI; output <song>.mid.wav at 44.1 kHz
    return f"opnmidiplay {base} {flag} {chips} {core_flag} {OPN_BANKS_DIR}/{bank_file} <song>.mid"


def edm_cmd(module: str, engines: dict | None) -> str:
    base = " ".join(_engine_args(engines, "edmidi", "base_args", DEFAULT_EDM_BASE_ARGS))
    return f"edmidi-render {base} -m {module} -o raw.wav <song>.mid"


def timidity_cmd(engines: dict | None) -> str:
    base = " ".join(_engine_args(engines, "timidity", "base_args", DEFAULT_TIMIDITY_BASE_ARGS))
    return f"timidity {base} -o raw.wav <song>.mid"


def sc55_cmd(romset: str, family: str, engines: dict | None) -> str:
    base = " ".join(_engine_args(engines, "sc55", "base_args", DEFAULT_SC55_BASE_ARGS))
    return f"nuked-sc55-render {base} -d /roms/{romset} --romset {family} -o raw.wav <song>.mid"


def munt_cmd(romset: str, model: str, engines: dict | None) -> str:
    base = " ".join(_engine_args(engines, "munt", "base_args", DEFAULT_MUNT_BASE_ARGS))
    return f"mt32emu-smf2wav {base} -m /roms/{romset} -i {model} -o raw.wav <song>.mid"


def _opn_license_flag(license_text: str | None) -> str:
    key = (license_text or "unknown").strip().lower()
    return OPN_LICENSE_FLAG.get(key, "unknown")


def opn_variants(opn_doc: dict | None, engines: dict | None) -> list[dict]:
    """Two variants per enabled bank in opn_banks.json: opn-<slug> (Nuked OPN2) and opn-<slug>-opna (MAME OPNA).

    A bank is eligible only when ``enabled`` is true and it has an ``image_path`` (the Dockerfile installs it).
    ``publish`` (default true), ``completeness``, ``bank_map`` and ``quality`` may be set per bank in opn_banks.json.
    """
    out: list[dict] = []
    for b in (opn_doc or {}).get("banks", []):
        if not b.get("enabled") or b.get("duplicate_of"):
            continue
        if not b.get("image_path"):
            raise ValueError(f"opn_banks.json bank {b['slug']!r} is enabled but has no image_path (not in the image)")
        file = os.path.basename(b["image_path"])
        lic_flag = _opn_license_flag(b.get("license"))
        legal = None
        if lic_flag == "unknown":
            legal = (f"libOPNMIDI fm_banks/{b['file']}: no licence stated ({b.get('license_note')}); same situation as the "
                     f"libADLMIDI embedded banks ({ISSUE_301_URL}); published by default, set enabled:false in opn_banks.json to withdraw")
        if b.get("publish_note"):
            legal = (legal + " | " if legal else "") + str(b["publish_note"])
        wanted_ids = set(b.get("variant_ids") or [])
        for suffix, slug_suffix, label_suffix, core, core_flag, chip, year, chip_name in OPN_PASSES:
            vid = f"opn-{b['slug']}{suffix}"
            if wanted_ids and vid not in wanted_ids:
                raise ValueError(f"opn_banks.json bank {b['slug']!r}: variant_ids does not list {vid}")
            label = b["name"] + (f" [{label_suffix}]" if label_suffix else "")
            out.append({
                "id": vid,
                "slug": f"{b['slug']}{slug_suffix}",
                "label": label,
                "engine": "opnmidi",
                "core": core,
                "chip_family": chip,
                "type": "fm",
                "facets": {
                    "engine": "opnmidi", "chip": chip, "type": "fm",
                    "completeness": b.get("completeness") or "full_gm",
                    "bank_map": b.get("bank_map") or "gm",
                    "size": "<2MB",
                    "lineage": "fm_bank",
                    "decade": decade_of(year),
                    "quality": list(b.get("quality") or []),
                    "instrument": None,
                },
                "year": year,
                "year_confidence": "medium",
                "publish": bool(b.get("publish", True)),
                "requires_rom": False,
                "bank": {
                    "kind": "file", "dir": "wopn", "file": file, "sha256": b.get("sha256"), "bytes": b.get("bytes"),
                    "name": b["name"], "slug": b["slug"], "pass": "opna" if suffix else None,
                },
                "source": {
                    "file": None, "sha256": b.get("sha256"), "bytes": b.get("bytes"), "sf2": None,
                    "url": (b.get("source") or {}).get("url"),
                    "license_flag": lic_flag,
                    "license": b.get("license"),
                    "license_note": b.get("license_note"),
                    "repo": (b.get("source") or {}).get("repo"),
                    "path": (b.get("source") or {}).get("path"),
                    "readme": (b.get("source") or {}).get("readme"),
                    "chip": chip_name,
                    "decade_basis": "hardware era of the emulated chip",
                },
                "render": {"cmd": opn_cmd(file, core_flag, engines), "core": core},
                "legal_note": legal,
                "aliases": [],
            })
    return out


def edm_variants(engines: dict | None) -> list[dict]:
    url = _engine_args(engines, "edmidi", "url", "https://github.com/Wohlstand/libEDMIDI")
    out = []
    for vid, module, label, chip_family, chip_facet, core, comp, bmap, year, note in EDM_MODULES:
        out.append({
            "id": vid, "slug": vid, "label": label, "engine": "edmidi", "core": core, "chip_family": chip_family, "type": "fm",
            "module": module,
            "facets": {"engine": "edmidi", "chip": chip_facet, "type": "fm", "completeness": comp, "bank_map": bmap,
                       "size": "<2MB", "lineage": "fm_bank", "decade": decade_of(year), "quality": [], "instrument": None},
            "year": year, "year_confidence": "medium", "publish": True, "requires_rom": False, "bank": None,
            "source": _hw_source(url, "free", license="zlib-style (libEDMIDI LICENSE.txt)", note=note),
            "render": {"cmd": edm_cmd(module, engines), "core": core, "module": module},
            "legal_note": None, "aliases": [],
        })
    return out


def gus_variants(engines: dict | None) -> list[dict]:
    return [{
        "id": "gus-freepats", "slug": "gus-freepats", "label": "FreePats GUS patches (TiMidity++)", "engine": "timidity",
        "core": "timidity", "chip_family": "gus", "type": "sampled",
        "facets": {"engine": "timidity", "chip": "gus", "type": "sampled", "completeness": "partial", "bank_map": "gm",
                   "size": "16-100MB", "lineage": "gravis", "decade": "1990s", "quality": ["miss_ins"], "instrument": None},
        "year": 1992, "year_confidence": "medium", "publish": True, "requires_rom": False, "bank": None,
        "source": _hw_source("https://freepats.zenvoid.org/", "gpl",
                             license="FreePats 2006 patch set GPL-2+ with the FreePats exception; TiMidity++ GPL-2+",
                             note="Gravis Ultrasound .pat format (GUS 1992); freepats 20060219 defines 72 of 128 GM melodic "
                                  "programs and 56 drum notes (bank 0 + drumset 0 in /etc/timidity/freepats.cfg) -> partial"),
        "render": {"cmd": timidity_cmd(engines), "core": "timidity"},
        "legal_note": None, "aliases": [],
    }]


def sc55_variants(engines: dict | None) -> list[dict]:
    url = _engine_args(engines, "sc55", "url", "https://github.com/jcmoyer/Nuked-SC55")
    families = list(SC55_FAMILIES)
    table = _engine_args(engines, "sc55", "romset_flag", None)
    if table:
        known = {f[0] for f in families}
        extra = sorted(set(table.values()) - known)
        if extra:
            raise ValueError(f"engines.json sc55.romset_flag names families unknown to variants.py: {extra}")
    out = []
    for family, label, year, note in families:
        romset = f"sc55-{family}"
        out.append({
            "id": romset, "slug": romset, "label": f"{label} (Nuked-SC55)", "engine": "sc55", "core": "nuked-sc55",
            "chip_family": "pcm_rom", "type": "sampled",
            "romset": romset, "rom": {"family": family, "dir": romset, "engine": "sc55"},
            "facets": {"engine": "sc55", "chip": "pcm_rom", "type": "sampled",
                       "completeness": "partial" if family == "jv880" else "full_gm",
                       "bank_map": "non_gm" if family == "jv880" else "gs_var", "size": "2-16MB", "lineage": "roland",
                       "decade": "1990s", "quality": ["non_gm"] if family == "jv880" else [], "instrument": None},
            "year": year, "year_confidence": "medium" if year else "low", "publish": True, "requires_rom": True, "bank": None,
            "source": _hw_source(url, "roland_copyright", license="engine: MAME-style non-commercial (Nuked-SC55 LICENSE); ROMs: Roland",
                                 note=note, romset_dir=f"roms/{romset}/"),
            "render": {"cmd": sc55_cmd(romset, family, engines), "core": "nuked-sc55"},
            "legal_note": SC55_NONCOMMERCIAL,
            "aliases": [],
        })
    return out


def munt_variants(engines: dict | None) -> list[dict]:
    url = _engine_args(engines, "munt", "url", "https://github.com/munt/munt")
    out = []
    for vid, model, romset, label, year, note in MUNT_MODELS:
        out.append({
            "id": vid, "slug": f"munt-{vid}", "label": f"{label} (Munt)", "engine": "munt", "core": "mt32emu", "chip_family": "la", "type": "la",
            "romset": romset, "model": model, "rom": {"machine": model, "dir": romset, "engine": "munt"},
            "facets": {"engine": "munt", "chip": "la", "type": "la", "completeness": "full_gm", "bank_map": "non_gm",
                       "size": "<2MB", "lineage": "roland", "decade": decade_of(year), "quality": ["mt32"], "instrument": None},
            "year": year, "year_confidence": "medium", "publish": True, "requires_rom": True, "bank": None,
            "source": _hw_source(url, "roland_copyright", license="engine: libmt32emu LGPL-2.1+, mt32emu-smf2wav GPL-3+; ROMs: Roland",
                                 note=note, romset_dir=f"roms/{romset}/"),
            "render": {"cmd": munt_cmd(romset, model, engines), "core": "mt32emu", "model": model},
            "legal_note": MUNT_LEGAL,
            "aliases": [],
        })
    return out


# ------------------------------------------------------------------ assembly


def build(facets_doc: dict, scan_doc: dict | None, banks_doc: dict, passes_doc: dict | None,
          opn_doc: dict | None = None, engines: dict | None = None, overrides_doc: dict | None = None,
          now: _dt.datetime | None = None) -> dict:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    sf2, aliases = sf2_variants(facets_doc, scan_doc, engines)
    adl, pass_counts = adl_variants(banks_doc, passes_doc, engines, load_adl_overrides(overrides_doc))
    opn = opn_variants(opn_doc, engines)
    edm = edm_variants(engines)
    gus = gus_variants(engines)
    sc55 = sc55_variants(engines)
    munt = munt_variants(engines)
    variants = sorted(sf2 + adl + opn + edm + gus + sc55 + munt, key=sort_key)
    apply_variant_overrides(variants, load_variant_overrides(overrides_doc))

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

    by_engine = count_values(v["engine"] for v in variants)
    by_chip = count_values(v["chip_family"] for v in variants)
    counts = {
        "total": len(variants),
        "published": sum(1 for v in variants if v["publish"]),
        "unpublished": sum(1 for v in variants if not v["publish"]),
        "sf2": len(sf2),
        "sf2_aliases": len(aliases),
        "adl": sum(1 for v in adl if not (v["bank"] or {}).get("pass")),
        **{f"adl_{k}": n for k, n in pass_counts.items()},
        "opn": sum(1 for v in opn if not v["bank"].get("pass")),
        "opn_opna": sum(1 for v in opn if v["bank"].get("pass") == "opna"),
        "opn_banks": len({v["bank"]["file"] for v in opn}),
        "edm": len(edm),
        "gus": len(gus),
        "sc55": len(sc55),
        "munt": len(munt),
        "requires_rom": sum(1 for v in variants if v["requires_rom"]),
        "by_engine": by_engine,
        "by_chip": by_chip,
    }

    opn_banks = (opn_doc or {}).get("banks", [])
    pending = {
        "opnmidi_disabled": {
            "source": "catalog/opn_banks.json",
            "note": "banks listed but enabled:false (not in the image: OPN2BankEditor-only files; or duplicates of a shipped bank)",
            "slugs": sorted(b["slug"] for b in opn_banks if not b.get("enabled")),
        },
        "edmidi_dropped": {"ids": ["edm-psg"], "note": "libEDMIDI has no PSG voice (emu2149 is compiled but never attached); see render/edmidi-render/README.md"},
        "adl_wopl": {"ids": ["adl-w-<sha256[:10]>"], "note": "extra full-GM .wopl from OPL3BankEditor with a license file (~5); none added yet (render/banks/README.md)"},
    }

    return {
        "schema": SCHEMA,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "_comment": [
            "Generated by python3 -m catalog.variants from soundfonts.facets.json + soundfonts.json + adl_banks.json + adl_passes.json + opn_banks.json + render/engines.json (+ overrides.json 'adl_banks'); do not edit (use overrides.json / adl_passes.json / opn_banks.json).",
            "ids are the render/cache identity and never change: sf2-<sha256[:10]> (rename-proof, byte-identical twins share one id and appear in 'aliases'), adl-b{NN}, adl-b{NN}-opl2|-esfmu|-cqm, opn-<slug>[-opna], edm-opll|scc|all, gus-freepats, sc55-<family>, mt32, cm32l.",
            "requires_rom variants (sc55-*, mt32, cm32l) carry 'romset' == the directory under roms/ that must hold the owner's ROM images; sfr plans them only when that directory exists.",
            "variants[] is in canonical order: " + " -> ".join(ORDER["keys"]) + " (value orders in 'order'); sfr pack cuts groups of 24 from this order, so keep it stable.",
            "name = bank number for embedded FM banks, case-folded label then file name for SoundFonts.",
            "facets.size is the facets-file size_bucket; facets.quality lists the bank flags (non_gm, mt32, miss_ins, broken_drums); bank.tags has the full boolean set incl. fourop/pseudo_fourop.",
            "render.cmd is a documentation template (<song> placeholder); sfr builds the real argv from render/engines.json.",
            "'pending' lists what is deliberately not a variant: disabled/duplicate WOPN banks, the impossible edm-psg, and the extra WOPL banks that have not been added.",
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


def _chips_text(opn_vs: list[dict]) -> str:
    for v in opn_vs:
        m = re.search(r"--chips (\d+)", v["render"]["cmd"])
        if m:
            return f"`--chips {m.group(1)}` = {6 * int(m.group(1))} FM voices"
    return "chips per engines.json"


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
             f"{c['adl']} libADLMIDI OPL3 + {c.get('adl_opl2', 0)} OPL2 pass + {c.get('adl_esfmu', 0)} ESFM + {c.get('adl_cqm', 0)} CQM + "
             f"{c['opn']} libOPNMIDI OPN2 + {c['opn_opna']} OPNA ({c['opn_banks']} WOPN banks) + {c['edm']} libEDMIDI + "
             f"{c['gus']} TiMidity/FreePats + {c['sc55']} Nuked-SC55 + {c['munt']} Munt; "
             f"{c['sf2_aliases']} byte-identical twins are aliases; {c['requires_rom']} variants need owner-supplied ROMs.")
    pend = doc["pending"]
    L.append(f"- Not variants: {len(pend['opnmidi_disabled']['slugs'])} disabled/duplicate WOPN entries "
             f"({', '.join(pend['opnmidi_disabled']['slugs'])}); `edm-psg` (no PSG voice in libEDMIDI); extra WOPL banks (none added).")
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

    # ---- OPN banks
    opn_vs = [v for v in doc["variants"] if v["engine"] == "opnmidi"]
    L.append(f"## libOPNMIDI WOPN banks ({c['opn_banks']} banks -> {len(opn_vs)} variants)")
    L.append("")
    if opn_doc:
        flagged_opn = sorted({v["bank"]["slug"] for v in opn_vs if v["source"]["license_flag"] == "unknown"})
        L.append(f"{opn_doc['count']} entries in `opn_banks.json`, {opn_doc['distinct_sha256']} distinct files, "
                 f"{sum(1 for b in opn_doc['banks'] if b.get('enabled'))} enabled. Every enabled bank yields `opn-<slug>` "
                 "(YM2612 via Nuked-OPN2, `--emu-nuked-3438`) and `opn-<slug>-opna` (YM2608 via MAME, `--emu-mame-opna`), "
                 f"{_chips_text(opn_vs)}. Banks shipped in libOPNMIDI's `fm_banks/` "
                 "without a licence statement are enabled (same treatment as the libADLMIDI banks under issue #301) and flagged: "
                 + (", ".join(f"`{s}`" for s in flagged_opn) or "none") + ". To withdraw one set `\"enabled\": false` "
                 "(or `\"publish\": false`) on the bank in `opn_banks.json` and re-run `python3 -m catalog.variants`.")
        L.append("")
        L.append(_table(["slug", "name", "licence", "enabled", "variants", "duplicate of", "note"],
                        [[b["slug"], b["name"], b["license"], "yes" if b.get("enabled") else "no",
                          ", ".join(v["id"] for v in opn_vs if v["bank"]["slug"] == b["slug"]) or "-",
                          b.get("duplicate_of") or "", b.get("license_note")] for b in opn_doc["banks"]]))
    else:
        L.append("`opn_banks.json` not available.")
    L.append("")

    # ---- EDMIDI / GUS
    other = [v for v in doc["variants"] if v["engine"] in ("edmidi", "timidity")]
    L.append(f"## libEDMIDI and TiMidity/FreePats ({len(other)} variants)")
    L.append("")
    L.append("libEDMIDI has no PSG voice (the planned `edm-psg` cannot exist) and `edm-scc` has no percussion by construction "
             "(channel 10 goes to the OPLL rhythm section). FreePats 20060219 defines 72 of the 128 GM melodic programs, so "
             "`gus-freepats` is `partial` with quality `miss_ins`.")
    L.append("")
    L.append(_table(["variant", "label", "chip", "completeness", "bank_map", "quality", "note"],
                    [[v["id"], v["label"], " + ".join(v["facets"]["chip"]) if isinstance(v["facets"]["chip"], list) else v["facets"]["chip"],
                      v["facets"]["completeness"], v["facets"]["bank_map"],
                      ", ".join(v["facets"]["quality"]), v["source"].get("note")] for v in other]))
    L.append("")

    # ---- per-variant overrides
    ov_vs = [v for v in doc["variants"] if v.get("override")]
    L.append(f"## Per-variant overrides in effect ({len(ov_vs)})")
    L.append("")
    L.append("`overrides.json` `variants` section: one entry per variant id (any engine). Used for single passes that the "
             "ESFM-vs-Nuked null test found identical (plan §6) and for withdrawing individual ROM-engine variants.")
    L.append("")
    L.append(_table(["variant", "publish", "label", "notes"],
                    [[v["id"], "yes" if v["publish"] else "NO", v["override"].get("label") or "", v["override"].get("notes") or ""] for v in ov_vs]))
    L.append("")

    # ---- ROM engines
    rom_vs = [v for v in doc["variants"] if v["requires_rom"]]
    L.append(f"## ROM-engine variants ({len(rom_vs)}, all `requires_rom`)")
    L.append("")
    L.append("Rendered only when `roms/<romset>/` exists on the render host (the ROM files never enter the repo or the image; "
             "`sfr` lists the sub-directories of `roms/` and skips the rest). Nuked-SC55 is under a MAME-style **non-commercial** "
             "licence and every one of these renders derives from Roland ROM images; all are published by default (decision D2). "
             "To withdraw one add `\"variants\": {\"<id>\": {\"publish\": false, \"notes\": \"...\"}}` to `overrides.json` "
             "(per-variant-id overrides, any engine; `label` and `notes` are also accepted).")
    L.append("")
    L.append(_table(["variant", "label", "engine", "romset dir", "flag", "completeness", "bank_map", "decade", "publish"],
                    [[v["id"], v["label"], v["engine"], f"roms/{v['romset']}/",
                      (v.get("rom") or {}).get("family") or (v.get("rom") or {}).get("machine"),
                      v["facets"]["completeness"], v["facets"]["bank_map"], v["facets"]["decade"], "yes" if v["publish"] else "NO"]
                     for v in rom_vs]))
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
        + " + ".join(f"{k[4:]} {v}" for k, v in c.items() if k.startswith("adl_"))
        + f" + opn {c['opn']} + opna {c['opn_opna']} + edm {c['edm']} + gus {c['gus']} + sc55 {c['sc55']} + munt {c['munt']}"
        + f"; aliases {c['sf2_aliases']}; requires_rom {c['requires_rom']}")
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
