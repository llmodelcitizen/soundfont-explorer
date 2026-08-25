"""Integration checks against the real 500-font collection.

These read the committed ``catalog/soundfonts.json`` / ``soundfonts.facets.json``
(fast: no SoundFont is opened).  The count assertions only run on a machine that
has the ``soundfonts/`` directory (the workstation); the consistency check that the two
committed artifacts agree with the code runs everywhere, including CI.
"""

import json
import os
import unittest

from catalog import build

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCAN_PATH = os.path.join(REPO, "catalog", "soundfonts.json")
FACETS_PATH = os.path.join(REPO, "catalog", "soundfonts.facets.json")
OVERRIDES_PATH = os.path.join(REPO, "catalog", "overrides.json")
HAVE_COLLECTION = os.path.isdir(os.path.join(REPO, "soundfonts"))

# Measured on 2026-08-21 over the 500 files of "500 Soundfonts Full GM Sets" with the
# plan §6 definitions (full_gm = >= 100 distinct bank-0 presets AND a bank-128 preset;
# the Drums_/Piano_/Guitar_/Bass_ filename prefix wins over the structural class).
#
# The research report quoted 409 / 26 / 34 / 6 / 72, which sums to 547 > 500, so it
# counted overlapping categories: its 409 "full_gm" equals our *structural* full_gm
# (405 + 4 single-instrument files that are really full GM sets: Drums_AWEGM,
# Drums_FantaGM 512, Drums_Heavy 500K, Guitar_Bass_Drums_MRGM), and its 72 matches our
# prefix count exactly; the 26 / 34 / 6 split cannot be reproduced from any of the
# definitions in the plan (structural gives 9 / 38 / 44).  The numbers below are the
# real ones with the precedence rule applied.
EXPECTED_COMPLETENESS = {"full_gm": 405, "melodic_only": 9, "drums_only": 1, "partial": 13, "single_instrument": 72}
EXPECTED_STRUCTURAL = {"full_gm": 409, "melodic_only": 9, "drums_only": 38, "partial": 44}
EXPECTED_INSTRUMENT = {"drums": 48, "guitar": 17, "piano": 5, "bass": 2}
EXPECTED_DUPLICATES = [
    ("8MBGSFX Custom.sf2", ["GM GSCustom Bank.sf2"]),
    ("Audio 100 Boba Blaster Bank.sf2", ["DGX 62M GM.sf2"]),
    ("GM GS MT32 v2.51 Bank.sf2", ["MT32 GS 2.51.sf2"]),
    ("MakeMusic 8.1.sf2", ["SynthGMS27MB.sf2"]),
    ("SONIVOX 4 Meg GM Set.sf2", ["Sonic Implants 04 MB GM.sf2"]),
]
GS_SOUND_SET_FILES = {
    "Eternal Hydrogen II - 3MGMGS.sf2",
    "FF7 SC-55 Hybrid.sf2",
    "SC-55 DJ Tony v1.1.sf2",
    "SCC1T2.sf2",
    "Windows.sf2",
}


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


@unittest.skipUnless(os.path.exists(SCAN_PATH) and os.path.exists(FACETS_PATH), "committed catalog artifacts missing")
class CommittedArtifactsConsistent(unittest.TestCase):
    """Rebuilding facets from the committed scan + overrides must reproduce the committed facets."""

    def test_facets_match_code(self):
        scan = _load(SCAN_PATH)
        committed = _load(FACETS_PATH)
        rebuilt = build.build(scan, build.load_overrides(OVERRIDES_PATH))
        for key in ("generated_at",):
            committed.pop(key, None)
            rebuilt.pop(key, None)
        self.assertEqual(rebuilt["counts"], committed["counts"])
        self.assertEqual(rebuilt["duplicates"], committed["duplicates"])
        self.assertEqual(rebuilt["lineage_regex"], committed["lineage_regex"])
        self.assertEqual(len(rebuilt["fonts"]), len(committed["fonts"]))
        for a, b in zip(rebuilt["fonts"], committed["fonts"]):
            self.assertEqual(a, b, f"facets for {a['file']} are stale; rerun python3 -m catalog.build")

    def test_scan_shape(self):
        scan = _load(SCAN_PATH)
        self.assertEqual(scan["schema"], 1)
        self.assertEqual(scan["count"], len(scan["fonts"]))
        self.assertEqual([f["file"] for f in scan["fonts"]], sorted(f["file"] for f in scan["fonts"]))
        for f in scan["fonts"]:
            self.assertEqual(len(f["sha256"]), 64, f["file"])
            self.assertTrue(f["file"].lower().endswith(".sf2"), f["file"])
            if f["parse_ok"]:
                self.assertIsNone(f["error"])
                self.assertEqual(sum(f["presets_by_bank"].values()), f["preset_count"], f["file"])
                self.assertEqual(f["has_drums"], 128 in f["banks"], f["file"])


@unittest.skipUnless(HAVE_COLLECTION, "soundfonts/ collection not present on this machine")
class RealCollectionCounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scan = _load(SCAN_PATH)
        cls.facets = _load(FACETS_PATH)
        cls.by_file = {f["file"]: f for f in cls.facets["fonts"]}

    def test_500_entries_all_parsed(self):
        self.assertEqual(self.scan["count"], 500)
        self.assertEqual(self.scan["parse_failures"], 0)
        self.assertEqual([f["file"] for f in self.scan["fonts"] if not f["parse_ok"]], [])
        self.assertEqual(self.facets["count"], 500)
        self.assertEqual(sum(1 for n in os.listdir(os.path.join(REPO, "soundfonts")) if n.lower().endswith(".sf2")), 500)

    def test_completeness_counts(self):
        self.assertEqual(self.facets["counts"]["completeness"], EXPECTED_COMPLETENESS)
        self.assertEqual(sum(EXPECTED_COMPLETENESS.values()), 500)
        self.assertEqual(self.facets["counts"]["completeness_structural"], EXPECTED_STRUCTURAL)
        self.assertEqual(self.facets["counts"]["instrument"], EXPECTED_INSTRUMENT)

    def test_five_byte_identical_pairs(self):
        dups = [(d["canonical"], d["twins"]) for d in self.facets["duplicates"]]
        self.assertEqual(dups, EXPECTED_DUPLICATES)
        self.assertEqual(self.facets["canonical_count"], 495)
        for canonical, twins in EXPECTED_DUPLICATES:
            for twin in twins:
                self.assertEqual(self.by_file[twin]["dup_of"], canonical)
                self.assertEqual(self.by_file[twin]["alias_of"], self.by_file[canonical]["variant_id"])
                self.assertIsNone(self.by_file[canonical]["dup_of"])

    def test_gs_sound_set_preseeded_as_roland(self):
        seeded = {f["file"] for f in self.facets["fonts"] if f["sources"]["lineage"] == "override"}
        self.assertEqual(seeded, GS_SOUND_SET_FILES)
        for name in GS_SOUND_SET_FILES:
            self.assertEqual(self.by_file[name]["facets"]["lineage"], "roland")
            self.assertEqual(self.by_file[name]["facets"]["license_flag"], "roland_copyright")
        self.assertEqual(self.facets["unused_override_keys"], [])

    def test_publish_defaults_true(self):
        self.assertEqual(self.facets["counts"]["publish"], {"true": 500})

    def test_facet_vocabularies(self):
        for f in self.facets["fonts"]:
            fx = f["facets"]
            self.assertIn(fx["completeness"], build.COMPLETENESS_VALUES)
            self.assertIn(fx["bank_map"], build.BANK_MAP_VALUES)
            self.assertIn(fx["size_bucket"], [b[0] for b in build.SIZE_BUCKETS])
            self.assertIn(fx["lineage"], build.LINEAGE_VALUES)
            self.assertIn(fx["year_confidence"], build.YEAR_CONFIDENCE)
            self.assertIn(fx["license_flag"], build.LICENSE_VALUES)
            self.assertTrue(fx["decade"] == "unknown" or fx["decade"].endswith("0s"))

    def test_size_buckets(self):
        self.assertEqual(self.facets["counts"]["size_bucket"][">1GB"], 6)
        self.assertEqual(sum(self.facets["counts"]["size_bucket"].values()), 500)


if __name__ == "__main__":
    unittest.main()
