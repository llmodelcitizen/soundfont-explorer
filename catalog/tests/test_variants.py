"""variants.py: stable ids, counts, canonical order, twin aliasing, ADL passes, review.md."""

import copy
import datetime as dt
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

from catalog import adlbanks, build, variants

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CATALOG = os.path.join(REPO, "catalog")
LEGACY = os.path.join(REPO, "legacy", "mvp", "data", "banks.txt")
NOW = dt.datetime(2026, 8, 21, tzinfo=dt.timezone.utc)

ENGINES = {
    "engines": {
        "adlmidi": {"base_args": ["-f32", "-vm", "0", "--gain", "2.0"], "chips": 1},
        "fluidsynth": {
            "base_args": ["-ni", "-T", "wav", "-O", "float", "-r", "48000", "-g", "0.5", "-R", "0", "-C", "0",
                          "-o", "synth.polyphony=256", "-o", "synth.cpu-cores=1"],
            "dynamic_sample_loading_above_bytes": 134217728,
        },
    }
}


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def scan_font(file, melodic=128, drums=True, banks=None, sha=None, nbytes=5_000_000, **info):
    base = {k: None for k in ("INAM", "ICRD", "IENG", "IPRD", "ICOP", "ICMT", "ISFT", "isng")}
    base.update(info)
    if banks is None:
        banks = ([0] if melodic else []) + ([128] if drums else [])
    return {
        "file": file, "bytes": nbytes, "sha256": sha or _sha(file), "ifil": "2.1", "info": base,
        "melodic_bank0": melodic, "has_drums": drums, "banks": banks,
        "preset_count": melodic + (1 if drums else 0), "presets_by_bank": {}, "parse_ok": True, "error": None, "warnings": [],
    }


SCAN = {
    "schema": 1, "generated_at": "2026-08-21T00:00:00Z", "root": "soundfonts", "count": 7, "parse_failures": 0,
    "timing_s": {}, "fonts": [
        scan_font("Zeta GM.sf2", INAM="Zeta", ICRD="1999"),
        scan_font("alpha gm.sf2", INAM="Alpha", ICRD="2005", nbytes=300 << 20),
        scan_font("Twin A.sf2", sha=_sha("twin"), INAM="Twin"),
        scan_font("Twin B.sf2", sha=_sha("twin"), INAM="Twin"),
        scan_font("SC-55 Thing.sf2", INAM="SC-55 Thing", ICOP="Copyright Roland 1996", nbytes=1 << 20),
        scan_font("Piano_Grand.sf2", melodic=1, drums=False),
        scan_font("Kit only.sf2", melodic=0, drums=True, banks=[128]),
    ],
}
OVERRIDES = {"lineage_regex": None, "ignore_values": None, "fonts": {}}

PASSES = {
    "schema": 1,
    "passes": [
        {"id": "opl2", "suffix": "-opl2", "core": "dosbox-opl2", "core_flag": "--emu-dosbox-opl2", "chip_family": "opl2",
         "label_suffix": "OPL2", "rationale": "t", "banks": [0, 3]},
        {"id": "esfmu", "suffix": "-esfmu", "core": "esfmu", "core_flag": "--emu-esfmu", "chip_family": "esfm",
         "label_suffix": "ESFM", "rationale": "t", "banks": [0]},
    ],
}


def _banks_doc():
    with open(LEGACY, encoding="utf-8") as fh:
        return adlbanks.build_doc(adlbanks.parse_listing(fh.read()), {"kind": "file"}, now=NOW)


def _small_banks_doc():
    listing = "\n".join([
        "0 = AIL (The Fat Man 2op set, default AIL)",
        "3 = HMI (Descent:: Int) :NON-GM:",
        "24 = AIL (When Two Worlds War) :MT-32: :MISS-INS:",
        "56 = SB (3d Cyberpuck :: melodic only)",
        "67 = TMB (Apogee Sound System Default bank) :broken drums:",
    ])
    return adlbanks.build_doc(adlbanks.parse_listing(listing), {"kind": "test"}, now=NOW)


class SyntheticBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facets = build.build(SCAN, OVERRIDES, now=NOW)
        cls.doc = variants.build(cls.facets, SCAN, _small_banks_doc(), PASSES, None, ENGINES, None, now=NOW)
        cls.by_id = {v["id"]: v for v in cls.doc["variants"]}

    def test_counts(self):
        c = self.doc["counts"]
        self.assertEqual(c["sf2"], 6)  # 7 fonts, one twin pair
        self.assertEqual(c["sf2_aliases"], 1)
        self.assertEqual(c["adl"], 5)
        self.assertEqual(c["adl_opl2"], 2)
        self.assertEqual(c["adl_esfmu"], 1)
        self.assertEqual(c["total"], 6 + 5 + 2 + 1)
        self.assertEqual(c["by_engine"], {"adlmidi": 8, "fluidsynth": 6})
        self.assertEqual(c["by_chip"], {"esfm": 1, "opl2": 2, "opl3": 5, "sf2": 6})

    def test_sf2_ids_are_sha_prefixed(self):
        v = self.by_id["sf2-" + _sha("Zeta GM.sf2")[:10]]
        self.assertEqual(v["source"]["file"], "Zeta GM.sf2")
        self.assertEqual(v["engine"], "fluidsynth")
        self.assertEqual(v["chip_family"], "sf2")
        self.assertEqual(v["type"], "sampled")
        self.assertEqual(v["slug"], "zeta-gm")
        self.assertEqual(v["facets"]["size"], "2-16MB")
        self.assertEqual(v["facets"]["decade"], "1990s")
        self.assertEqual(v["facets"]["quality"], [])
        self.assertEqual(v["source"]["sf2"]["INAM"], "Zeta")
        self.assertFalse(v["requires_rom"])
        self.assertIsNone(v["bank"])

    def test_twins_become_one_variant_plus_alias(self):
        vid = "sf2-" + _sha("twin")[:10]
        v = self.by_id[vid]
        self.assertEqual(v["source"]["file"], "Twin A.sf2")
        self.assertEqual(v["aliases"], [{"file": "Twin B.sf2", "label": "Twin B"}])
        self.assertEqual(self.doc["aliases"], [{
            "file": "Twin B.sf2", "label": "Twin B", "variant_id": vid, "canonical_file": "Twin A.sf2", "sha256": _sha("twin"),
        }])
        self.assertEqual(sum(1 for x in self.doc["variants"] if x["source"].get("sha256") == _sha("twin")), 1)
        self.assertTrue(all("alias_of" not in x for x in self.doc["variants"]))

    def test_fluidsynth_cmd_template(self):
        small = self.by_id["sf2-" + _sha("Zeta GM.sf2")[:10]]["render"]["cmd"]
        self.assertEqual(
            small,
            "fluidsynth -F raw.wav -ni -T wav -O float -r 48000 -g 0.5 -R 0 -C 0 -o synth.polyphony=256 -o synth.cpu-cores=1 "
            "/fonts/'Zeta GM.sf2' <song>.mid",
        )
        big = self.by_id["sf2-" + _sha("alpha gm.sf2")[:10]]["render"]["cmd"]
        self.assertIn("-o synth.dynamic-sample-loading=1 /fonts/'alpha gm.sf2'", big)
        self.assertNotIn("dynamic-sample-loading", small)

    def test_roland_legal_note(self):
        v = self.by_id["sf2-" + _sha("SC-55 Thing.sf2")[:10]]
        self.assertEqual(v["source"]["license_flag"], "roland_copyright")
        self.assertIn("Roland", v["legal_note"])
        self.assertTrue(v["publish"])
        self.assertIsNone(self.by_id["sf2-" + _sha("Zeta GM.sf2")[:10]]["legal_note"])

    def test_single_instrument_and_drums_only_facets(self):
        piano = self.by_id["sf2-" + _sha("Piano_Grand.sf2")[:10]]
        self.assertEqual(piano["facets"]["completeness"], "single_instrument")
        self.assertEqual(piano["facets"]["instrument"], "piano")
        kit = self.by_id["sf2-" + _sha("Kit only.sf2")[:10]]
        self.assertEqual(kit["facets"]["completeness"], "drums_only")

    def test_adl_variants(self):
        v = self.by_id["adl-b0"]
        self.assertEqual(v["render"]["cmd"], "adlmidiplay <song>.mid -f32 -vm 0 --gain 2.0 --emu-nuked 0 1")
        self.assertEqual(v["render"]["core"], "nuked")
        self.assertEqual((v["engine"], v["chip_family"], v["type"]), ("adlmidi", "opl3", "fm"))
        self.assertEqual(v["facets"]["completeness"], "full_gm")
        self.assertEqual(v["facets"]["lineage"], "fm_bank")
        self.assertEqual(v["facets"]["size"], "<2MB")
        self.assertEqual(v["facets"]["bank_map"], "gm")
        self.assertEqual(v["facets"]["decade"], "1990s")
        self.assertEqual(v["bank"]["kind"], "embedded")
        self.assertEqual(v["bank"]["number"], 0)
        self.assertEqual(v["bank"]["family"], "AIL")
        self.assertIsNone(v["bank"]["pass"])
        self.assertEqual(v["label"], adlbanks.LABEL_OVERRIDES[0])
        self.assertEqual(v["source"]["license_flag"], "free")
        self.assertIsNone(v["source"]["file"])
        self.assertIsNone(v["legal_note"])

    def test_adl_tag_facets(self):
        non_gm = self.by_id["adl-b3"]
        self.assertEqual(non_gm["facets"]["completeness"], "partial")
        self.assertEqual(non_gm["facets"]["bank_map"], "non_gm")
        self.assertEqual(non_gm["facets"]["quality"], ["non_gm"])
        self.assertTrue(non_gm["bank"]["tags"]["non_gm"])
        self.assertIn("game_release", non_gm["legal_note"])
        mt = self.by_id["adl-b24"]
        self.assertEqual(mt["facets"]["completeness"], "partial")  # miss_ins
        self.assertEqual(mt["facets"]["quality"], ["mt32", "miss_ins"])
        mel = self.by_id["adl-b56"]
        self.assertEqual(mel["facets"]["completeness"], "melodic_only")
        self.assertEqual(mel["facets"]["bank_map"], "melodic_only")
        self.assertEqual(self.by_id["adl-b67"]["facets"]["quality"], ["broken_drums"])

    def test_adl_passes(self):
        opl2 = self.by_id["adl-b0-opl2"]
        self.assertEqual(opl2["render"]["cmd"], "adlmidiplay <song>.mid -f32 -vm 0 --gain 2.0 --emu-dosbox-opl2 0 1")
        self.assertEqual((opl2["core"], opl2["chip_family"]), ("dosbox-opl2", "opl2"))
        self.assertEqual(opl2["bank"]["pass"], "opl2")
        self.assertTrue(opl2["label"].endswith("[OPL2]"))
        self.assertEqual(opl2["slug"], self.by_id["adl-b0"]["slug"] + "-opl2")
        es = self.by_id["adl-b0-esfmu"]
        self.assertEqual(es["render"]["cmd"], "adlmidiplay <song>.mid -f32 -vm 0 --gain 2.0 --emu-esfmu 0 1")
        self.assertEqual((es["core"], es["chip_family"]), ("esfmu", "esfm"))
        self.assertIn("adl-b3-opl2", self.by_id)
        self.assertNotIn("adl-b24-opl2", self.by_id)

    def test_canonical_order(self):
        ids = [v["id"] for v in self.doc["variants"]]
        # engine block: all adlmidi first, then fluidsynth
        engines = [v["engine"] for v in self.doc["variants"]]
        self.assertEqual(engines, sorted(engines, key=variants.ORDER["engine"].index))
        # chip blocks inside adlmidi: opl3, then opl2, then esfm
        chips = [v["chip_family"] for v in self.doc["variants"] if v["engine"] == "adlmidi"]
        self.assertEqual(chips, ["opl3"] * 5 + ["opl2"] * 2 + ["esfm"])
        # inside opl3: full_gm banks (0, 67) by number, then melodic_only (56), then partial (3, 24)
        self.assertEqual(ids[:5], ["adl-b0", "adl-b67", "adl-b56", "adl-b3", "adl-b24"])
        # sf2: full_gm first; inside it lineage roland (SC-55 Thing) before generic; inside generic the 2-16MB
        # fonts by label casefold (Twin, Zeta) before the 100MB-1GB one (alpha); then drums_only; then single_instrument
        sf2 = [v["source"]["file"] for v in self.doc["variants"] if v["engine"] == "fluidsynth"]
        self.assertEqual(sf2, ["SC-55 Thing.sf2", "Twin A.sf2", "Zeta GM.sf2", "alpha gm.sf2", "Kit only.sf2", "Piano_Grand.sf2"])

    def test_order_is_deterministic_under_input_shuffle(self):
        rnd = random.Random(7)
        for _ in range(5):
            scan = copy.deepcopy(SCAN)
            rnd.shuffle(scan["fonts"])
            facets = build.build(scan, OVERRIDES, now=NOW)
            doc = variants.build(facets, scan, _small_banks_doc(), PASSES, None, ENGINES, None, now=NOW)
            self.assertEqual([v["id"] for v in doc["variants"]], [v["id"] for v in self.doc["variants"]])
            self.assertEqual(doc["variants"], self.doc["variants"])

    def test_ids_and_slugs_unique(self):
        ids = [v["id"] for v in self.doc["variants"]]
        slugs = [v["slug"] for v in self.doc["variants"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_facet_counts(self):
        self.assertEqual(self.doc["facets"]["engine"], {"adlmidi": 8, "fluidsynth": 6})
        self.assertEqual(self.doc["facets"]["quality"]["non_gm"], 2)  # adl-b3 + adl-b3-opl2
        self.assertEqual(self.doc["facets"]["lineage"]["fm_bank"], 8)

    def test_unknown_pass_bank_raises(self):
        bad = copy.deepcopy(PASSES)
        bad["passes"][0]["banks"].append(99)
        with self.assertRaises(ValueError):
            variants.build(self.facets, SCAN, _small_banks_doc(), bad, None, ENGINES, None, now=NOW)

    def test_adl_overrides(self):
        ov = {"adl_banks": {"3": {"publish": False, "notes": "withdrawn for the test"}, "0": {"label": "Default AIL"}}}
        doc = variants.build(self.facets, SCAN, _small_banks_doc(), PASSES, None, ENGINES, ov, now=NOW)
        by = {v["id"]: v for v in doc["variants"]}
        self.assertFalse(by["adl-b3"]["publish"])
        self.assertFalse(by["adl-b3-opl2"]["publish"])
        self.assertIn("withdrawn for the test", by["adl-b3"]["legal_note"])
        self.assertEqual(by["adl-b0"]["label"], "Default AIL")
        self.assertEqual(by["adl-b0-opl2"]["label"], "Default AIL [OPL2]")
        self.assertEqual(doc["counts"]["unpublished"], 2)
        with self.assertRaises(ValueError):
            variants.build(self.facets, SCAN, _small_banks_doc(), PASSES, None, ENGINES, {"adl_banks": {"3": {"bogus": 1}}}, now=NOW)
        with self.assertRaises(ValueError):
            variants.build(self.facets, SCAN, _small_banks_doc(), PASSES, None, ENGINES, {"adl_banks": {"99": {"publish": False}}}, now=NOW)

    def test_slug_collision_gets_sha_suffix(self):
        scan = copy.deepcopy(SCAN)
        scan["fonts"].append(scan_font("zeta-gm.SF2", INAM="Zeta 2"))
        facets = build.build(scan, OVERRIDES, now=NOW)
        doc = variants.build(facets, scan, _small_banks_doc(), PASSES, None, ENGINES, None, now=NOW)
        slugs = sorted(v["slug"] for v in doc["variants"] if v["slug"].startswith("zeta-gm"))
        self.assertEqual(len(slugs), 2)
        self.assertIn("zeta-gm", slugs)
        self.assertTrue(any(s.startswith("zeta-gm-") for s in slugs))

    def test_review_markdown_sections(self):
        md = variants.review_markdown(self.doc, self.facets, _small_banks_doc(), None)
        for heading in ("## Summary", "## Roland copyright fonts (1)", "## Regex-only lineage", "## Low-confidence years",
                        "## Byte-identical duplicates (1 groups)", "## libADLMIDI embedded banks and issue #301",
                        "## ROM-engine variants (M2b placeholder)", "## libOPNMIDI banks (M2b placeholder)"):
            self.assertIn(heading, md)
        self.assertIn("SC-55 Thing.sf2", md)
        self.assertIn("Twin B.sf2", md)
        self.assertIn(variants.ISSUE_301_URL, md)
        self.assertIn("| 3 | adl-b3, adl-b3-opl2 |", md)


@unittest.skipUnless(os.path.exists(LEGACY), "legacy listing missing")
class AllBanksWithRepoPasses(unittest.TestCase):
    def test_pass_counts_from_repo_passes_file(self):
        with open(os.path.join(CATALOG, "adl_passes.json"), encoding="utf-8") as fh:
            passes = json.load(fh)
        by_id = {p["id"]: p for p in passes["passes"]}
        self.assertEqual(by_id["opl2"]["banks"], [0, 2] + list(range(7, 17)) + list(range(55, 59)) + list(range(62, 68)) + list(range(69, 72)) + [76, 77])
        self.assertEqual(by_id["esfmu"]["banks"], [0, 14, 15, 58, 62, 65, 66, 67, 72, 77])
        self.assertEqual(by_id["cqm"]["banks"], by_id["esfmu"]["banks"])
        self.assertEqual((by_id["opl2"]["core_flag"], by_id["esfmu"]["core_flag"], by_id["cqm"]["core_flag"]),
                         ("--emu-dosbox-opl2", "--emu-esfmu", "--emu-nuked-cqm"))
        for p in passes["passes"]:
            self.assertTrue(p["rationale"])
        empty_facets = {"schema": 1, "generated_at": "x", "fonts": [], "counts": {}, "duplicates": []}
        doc = variants.build(empty_facets, None, _banks_doc(), passes, None, ENGINES, None, now=NOW)
        c = doc["counts"]
        self.assertEqual((c["adl"], c["adl_opl2"], c["adl_esfmu"], c["adl_cqm"], c["sf2"]), (79, 27, 10, 10, 0))
        self.assertEqual(c["total"], 126)
        # no 4-op bank in the OPL2 pass
        by = {v["id"]: v for v in doc["variants"]}
        for n in by_id["opl2"]["banks"]:
            self.assertFalse(by[f"adl-b{n}-opl2"]["bank"]["tags"]["fourop"], n)
            self.assertFalse(by[f"adl-b{n}-opl2"]["bank"]["tags"]["non_gm"], n)


@unittest.skipUnless(os.path.exists(os.path.join(CATALOG, "variants.json")), "committed variants.json missing")
class CommittedVariantsConsistent(unittest.TestCase):
    """Rebuilding from the committed inputs must reproduce the committed variants.json (apart from generated_at)."""

    @classmethod
    def setUpClass(cls):
        def load(name, required=True):
            p = os.path.join(CATALOG, name)
            if not os.path.exists(p):
                if required:
                    raise FileNotFoundError(p)
                return None
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)

        cls.committed = load("variants.json")
        engines_path = os.path.join(REPO, "render", "engines.json")
        engines = None
        if os.path.exists(engines_path):
            with open(engines_path, encoding="utf-8") as fh:
                engines = json.load(fh)
        cls.rebuilt = variants.build(load("soundfonts.facets.json"), load("soundfonts.json", False), load("adl_banks.json"),
                                     load("adl_passes.json", False), load("opn_banks.json", False), engines, load("overrides.json", False))

    def test_reproducible(self):
        a, b = dict(self.committed), dict(self.rebuilt)
        a.pop("generated_at"), b.pop("generated_at")
        self.assertEqual(a["counts"], b["counts"])
        self.assertEqual(a["facets"], b["facets"])
        self.assertEqual(a["aliases"], b["aliases"])
        self.assertEqual(len(a["variants"]), len(b["variants"]))
        for x, y in zip(a["variants"], b["variants"]):
            self.assertEqual(x, y, f"{x['id']} stale; rerun python3 -m catalog.variants")

    def test_shape(self):
        doc = self.committed
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["order"]["keys"], ["engine", "chip", "completeness", "lineage", "size", "name", "id"])
        ids = [v["id"] for v in doc["variants"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [v["id"] for v in sorted(doc["variants"], key=variants.sort_key)])
        required = {"id", "slug", "label", "engine", "core", "chip_family", "type", "facets", "publish", "source", "requires_rom",
                    "render", "legal_note", "aliases", "bank"}
        for v in doc["variants"]:
            self.assertTrue(required <= set(v), v["id"])
            self.assertIn("cmd", v["render"])
            self.assertIn("core", v["render"])
            for k in ("engine", "chip", "type", "completeness", "bank_map", "size", "lineage", "decade", "quality"):
                self.assertIn(k, v["facets"], v["id"])
            if v["engine"] == "fluidsynth":
                self.assertRegex(v["id"], r"^sf2-[0-9a-f]{10}$")
                self.assertEqual(v["id"], "sf2-" + v["source"]["sha256"][:10])
            else:
                self.assertRegex(v["id"], r"^adl-b\d+(-opl2|-esfmu|-cqm)?$")
        alias_targets = {a["variant_id"] for a in doc["aliases"]}
        self.assertTrue(alias_targets <= set(ids))


@unittest.skipUnless(os.path.isdir(os.path.join(REPO, "soundfonts")), "soundfonts/ collection not present on this machine")
class RealCollectionVariantCounts(unittest.TestCase):
    def test_counts(self):
        with open(os.path.join(CATALOG, "variants.json"), encoding="utf-8") as fh:
            c = json.load(fh)["counts"]
        self.assertEqual(c["sf2"], 495)
        self.assertEqual(c["sf2_aliases"], 5)
        self.assertEqual(c["adl"], 79)
        self.assertEqual(c["adl_opl2"], 27)
        self.assertEqual(c["adl_esfmu"], 10)
        self.assertEqual(c["adl_cqm"], 10)
        self.assertEqual(c["total"], 621)
        self.assertEqual(c["by_chip"], {"cqm": 10, "esfm": 10, "opl2": 27, "opl3": 79, "sf2": 495})

    def test_cli_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "v.json")
            rev = os.path.join(td, "r.md")
            proc = subprocess.run([sys.executable, "-m", "catalog.variants", "--out", out, "--review", rev, "--quiet"],
                                  cwd=REPO, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["counts"]["total"], 621)
            with open(rev, encoding="utf-8") as fh:
                self.assertIn("## Roland copyright fonts (38)", fh.read())


if __name__ == "__main__":
    unittest.main()
