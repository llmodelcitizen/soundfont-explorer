"""adlbanks.py: parsing of ``adlmidiplay --list-banks`` (from the MVP's captured listing, no docker)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from catalog import adlbanks

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LEGACY = os.path.join(REPO, "catalog", "tests", "fixtures", "adlmidiplay-banks.txt")
COMMITTED = os.path.join(REPO, "catalog", "adl_banks.json")

SAMPLE = """\
==========================================
         libADLMIDI demo utility
==========================================

    Available embedded banks by number:

    Banks: 0 = AIL (The Fat Man 2op set, default AIL)
           3 = HMI (Descent:: Int) :NON-GM:
          20 = AIL (Guilty, Orion Conspiracy, TNSFC ::4op)
          24 = AIL (When Two Worlds War) :MT-32: :MISS-INS:
          36 = AIL (Super Street Fighter 2 :4op:)
          40 = AIL (Caesar 2) :p4op: :MISS-INS:
          54 = AIL (Master of Magic) :4op: orchestral drums
          56 = SB (3d Cyberpuck :: melodic only)
          66 = SB (Jamie O'Connell's bank)
          67 = TMB (Apogee Sound System Default bank) :broken drums:

     Use banks 2-5 to play Descent "q" soundtracks.
"""


def _on(tags):
    return sorted(k for k, v in tags.items() if v)


class ParseLine(unittest.TestCase):
    def test_plain(self):
        b = adlbanks.parse_line("    Banks: 0 = AIL (The Fat Man 2op set, default AIL)")
        self.assertEqual((b["index"], b["family"], b["name"]), (0, "AIL", "The Fat Man 2op set, default AIL"))
        self.assertEqual(_on(b["tags"]), [])
        self.assertEqual(b["slug"], "0-ail-the-fat-man-2op-set-default-ail")
        self.assertEqual(b["unknown_markers"], [])

    def test_mvp_format_without_banks_prefix(self):
        b = adlbanks.parse_line("3 = HMI (Descent:: Int) :NON-GM:")
        self.assertEqual((b["index"], b["family"], b["name"]), (3, "HMI", "Descent: Int"))
        self.assertEqual(_on(b["tags"]), ["non_gm"])
        self.assertEqual(b["slug"], "3-hmi-descent-int")

    def test_double_colon_tag_inside_parens(self):
        b = adlbanks.parse_line("20 = AIL (Guilty, Orion Conspiracy, TNSFC ::4op)")
        self.assertEqual(b["name"], "Guilty, Orion Conspiracy, TNSFC")
        self.assertEqual(_on(b["tags"]), ["fourop"])
        b = adlbanks.parse_line("56 = SB (3d Cyberpuck :: melodic only)")
        self.assertEqual(b["name"], "3d Cyberpuck")
        self.assertEqual(_on(b["tags"]), ["melodic_only"])

    def test_marker_inside_parens(self):
        b = adlbanks.parse_line("36 = AIL (Super Street Fighter 2 :4op:)")
        self.assertEqual(b["name"], "Super Street Fighter 2")
        self.assertEqual(_on(b["tags"]), ["fourop"])

    def test_multiple_markers(self):
        b = adlbanks.parse_line("24 = AIL (When Two Worlds War) :MT-32: :MISS-INS:")
        self.assertEqual(_on(b["tags"]), ["miss_ins", "mt32"])
        b = adlbanks.parse_line("40 = AIL (Caesar 2) :p4op: :MISS-INS:")
        self.assertEqual(_on(b["tags"]), ["miss_ins", "pseudo_fourop"])
        self.assertFalse(b["tags"]["fourop"])

    def test_trailer_text_and_broken_drums(self):
        b = adlbanks.parse_line("54 = AIL (Master of Magic) :4op: orchestral drums")
        self.assertEqual(b["name"], "Master of Magic, orchestral drums")
        self.assertEqual(_on(b["tags"]), ["fourop"])
        self.assertEqual(b["slug"], "54-ail-master-of-magic-orchestral-drums")
        b = adlbanks.parse_line("67 = TMB (Apogee Sound System Default bank) :broken drums:")
        self.assertEqual(_on(b["tags"]), ["broken_drums"])

    def test_apostrophe_in_slug(self):
        b = adlbanks.parse_line("66 = SB (Jamie O'Connell's bank)")
        self.assertEqual(b["slug"], "66-sb-jamie-o-connell-s-bank")

    def test_unknown_marker_is_reported_not_fatal(self):
        b = adlbanks.parse_line("9 = HMI (Theme Park) :WEIRD:")
        self.assertEqual(b["unknown_markers"], ["WEIRD"])
        self.assertEqual(_on(b["tags"]), [])

    def test_non_bank_lines_are_skipped(self):
        self.assertIsNone(adlbanks.parse_line("    Available embedded banks by number:"))
        self.assertIsNone(adlbanks.parse_line('     Use banks 2-5 to play Descent "q" soundtracks.'))
        self.assertIsNone(adlbanks.parse_line(""))

    def test_malformed_entry_raises(self):
        with self.assertRaises(adlbanks.ParseError):
            adlbanks.parse_line("7 = no parentheses here")


class ParseListing(unittest.TestCase):
    def test_sample(self):
        banks = adlbanks.parse_listing(SAMPLE)
        self.assertEqual([b["index"] for b in banks], [0, 3, 20, 24, 36, 40, 54, 56, 66, 67])
        self.assertEqual(set(b["family"] for b in banks), {"AIL", "HMI", "SB", "TMB"})

    def test_duplicate_index_raises(self):
        with self.assertRaises(adlbanks.ParseError):
            adlbanks.parse_listing("1 = AIL (a)\n1 = AIL (b)\n")

    def test_out_of_order_raises(self):
        with self.assertRaises(adlbanks.ParseError):
            adlbanks.parse_listing("2 = AIL (a)\n1 = AIL (b)\n")

    def test_enrich_and_doc(self):
        doc = adlbanks.build_doc(adlbanks.parse_listing(SAMPLE), {"kind": "test"})
        self.assertEqual(doc["count"], 10)
        self.assertEqual(doc["unknown_families"], [])
        b0 = doc["banks"][0]
        self.assertEqual(b0["label"], adlbanks.LABEL_OVERRIDES[0])
        self.assertEqual(b0["provenance"]["license_hint"], "mit")
        self.assertIn(b0["provenance"]["license_hint"], adlbanks.LICENSE_HINTS)
        self.assertEqual(doc["tag_counts"]["fourop"], 3)
        self.assertEqual(doc["tag_counts"]["miss_ins"], 2)


@unittest.skipUnless(os.path.exists(LEGACY), "catalog/tests/fixtures/adlmidiplay-banks.txt missing")
class LegacyListing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(LEGACY, encoding="utf-8") as fh:
            cls.banks = adlbanks.parse_listing(fh.read())
        cls.doc = adlbanks.build_doc(cls.banks, {"kind": "file", "path": "catalog/tests/fixtures/adlmidiplay-banks.txt"})

    def test_79_banks_contiguous(self):
        self.assertEqual(len(self.banks), adlbanks.EXPECTED_COUNT)
        self.assertEqual([b["index"] for b in self.banks], list(range(79)))

    def test_families_are_the_plans_list(self):
        self.assertEqual(set(b["family"] for b in self.banks), set(adlbanks.FAMILIES))
        self.assertEqual(self.doc["unknown_families"], [])

    def test_no_unknown_markers(self):
        self.assertEqual(self.doc["unknown_markers"], [])
        self.assertEqual([b["index"] for b in self.banks if b["unknown_markers"]], [])

    def test_tag_counts(self):
        self.assertEqual(self.doc["tag_counts"], {
            "non_gm": 6, "mt32": 15, "miss_ins": 5, "broken_drums": 1, "melodic_only": 3, "fourop": 6, "pseudo_fourop": 4,
        })
        by = {b["index"]: b for b in self.banks}
        self.assertEqual(sorted(i for i, b in by.items() if b["tags"]["non_gm"]), [3, 4, 5, 6, 43, 75])
        self.assertEqual(sorted(i for i, b in by.items() if b["tags"]["melodic_only"]), [56, 60, 61])
        self.assertEqual(sorted(i for i, b in by.items() if b["tags"]["miss_ins"]), [24, 25, 37, 40, 52])
        self.assertEqual(sorted(i for i, b in by.items() if b["tags"]["pseudo_fourop"]), [40, 44, 45, 48])

    def test_slugs_unique_and_stable(self):
        slugs = [b["slug"] for b in self.banks]
        self.assertEqual(len(set(slugs)), 79)
        by = {b["index"]: b for b in self.banks}
        self.assertEqual(by[58]["slug"], "58-wopl-the-fat-man-2op-set-win9x")
        self.assertEqual(by[72]["slug"], "72-wopl-dmxopl3-bank-by-sneakernets")
        self.assertEqual(by[15]["slug"], "15-dmx-cygnus-studios-default-dmx")

    def test_every_bank_has_provenance(self):
        for b in self.doc["banks"]:
            self.assertIn(b["provenance"]["license_hint"], adlbanks.LICENSE_HINTS, b["index"])
            self.assertTrue(b["provenance"]["note"], b["index"])
            self.assertEqual(set(b["tags"]), set(adlbanks.TAG_KEYS))
        self.assertEqual(set(adlbanks.PROVENANCE), set(range(79)))

    def test_cli_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "adl.json")
            proc = subprocess.run([sys.executable, "-m", "catalog.adlbanks", "--from-file", LEGACY, "--out", out, "--quiet"],
                                  cwd=REPO, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["count"], 79)
            self.assertEqual(doc["source"]["kind"], "file")

    def test_cli_expect_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "adl.json")
            proc = subprocess.run([sys.executable, "-m", "catalog.adlbanks", "--from-file", LEGACY, "--out", out, "--expect", "5", "--quiet"],
                                  cwd=REPO, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertFalse(os.path.exists(out))


@unittest.skipUnless(os.path.exists(COMMITTED) and os.path.exists(LEGACY), "committed adl_banks.json missing")
class CommittedMatchesLegacy(unittest.TestCase):
    """The committed file (captured from the sfr-render image) parses to the same banks as the MVP capture."""

    def test_same_banks(self):
        with open(COMMITTED, encoding="utf-8") as fh:
            committed = json.load(fh)
        with open(LEGACY, encoding="utf-8") as fh:
            legacy = adlbanks.build_doc(adlbanks.parse_listing(fh.read()), {"kind": "file"})
        self.assertEqual(committed["schema"], adlbanks.SCHEMA)
        self.assertEqual(committed["count"], 79)
        self.assertEqual(len(committed["banks"]), len(legacy["banks"]))
        for a, b in zip(committed["banks"], legacy["banks"]):
            for key in ("index", "family", "name", "tags", "slug", "label", "provenance"):
                self.assertEqual(a[key], b[key], f"bank {a['index']} {key} stale; rerun python3 -m catalog.adlbanks")
        self.assertEqual(committed["tag_counts"], legacy["tag_counts"])
        self.assertEqual(committed["license_hint_counts"], legacy["license_hint_counts"])


if __name__ == "__main__":
    unittest.main()
