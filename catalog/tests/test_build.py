import datetime as dt
import json
import os
import tempfile
import unittest

from catalog import build

NOW = dt.datetime(2026, 8, 21, tzinfo=dt.timezone.utc)
NOW_YEAR = NOW.year
COMPILED = build.compile_lineage_regex(build.DEFAULT_LINEAGE_REGEX)


def font(file, melodic=128, drums=True, banks=None, sha=None, nbytes=5_000_000, **info):
    base = {k: None for k in ("INAM", "ICRD", "IENG", "IPRD", "ICOP", "ICMT", "ISFT", "isng")}
    base.update(info)
    if banks is None:
        banks = ([0] if melodic else []) + ([128] if drums else [])
    return {
        "file": file,
        "bytes": nbytes,
        "sha256": sha or ("%064x" % (abs(hash(file)) % (1 << 200))),
        "ifil": "2.1",
        "info": base,
        "melodic_bank0": melodic,
        "has_drums": drums,
        "banks": banks,
        "preset_count": melodic + (1 if drums else 0),
        "presets_by_bank": {},
        "parse_ok": True,
        "error": None,
        "warnings": [],
    }


class CompletenessTests(unittest.TestCase):
    def test_structural(self):
        self.assertEqual(build.structural_completeness(128, True), "full_gm")
        self.assertEqual(build.structural_completeness(100, True), "full_gm")  # threshold inclusive
        self.assertEqual(build.structural_completeness(99, True), "partial")
        self.assertEqual(build.structural_completeness(128, False), "melodic_only")
        self.assertEqual(build.structural_completeness(0, True), "drums_only")
        self.assertEqual(build.structural_completeness(1, True), "partial")
        self.assertEqual(build.structural_completeness(0, False), "partial")

    def test_single_instrument_prefix_wins(self):
        for name in ("Drums_X.sf2", "Piano_X.sf2", "Guitar_X.sf2", "Bass_X.sf2", "drums_lower.SF2"):
            self.assertEqual(build.completeness(name, 128, True), "single_instrument", name)
        self.assertEqual(build.single_instrument_kind("Guitar_Bass_Drums_MRGM.sf2"), "guitar")
        self.assertIsNone(build.single_instrument_kind("Drumm GM.sf2"))
        self.assertIsNone(build.single_instrument_kind("The Piano_Font.sf2"))  # prefix only
        self.assertEqual(build.completeness("Drumm GM.sf2", 128, True), "full_gm")


class BankMapTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(build.bank_map([128]), "drums_only")
        self.assertEqual(build.bank_map([127, 128]), "drums_only")
        self.assertEqual(build.bank_map([64, 127, 128], "Kit XG"), "drums_only")  # no bank 0 -> drums first
        self.assertEqual(build.bank_map([]), "melodic_only")  # degenerate: no presets at all
        self.assertEqual(build.bank_map([0]), "melodic_only")
        self.assertEqual(build.bank_map([0, 1, 8]), "melodic_only")
        self.assertEqual(build.bank_map([0, 128]), "gm")
        self.assertEqual(build.bank_map([0, 1, 2, 8, 16, 127, 128]), "gs_var")
        self.assertEqual(build.bank_map([0, 126, 127, 128]), "gs_var")
        self.assertEqual(build.bank_map([0, 121, 128]), "gm2")
        self.assertEqual(build.bank_map([0, 1, 120, 128]), "gm2")
        self.assertEqual(build.bank_map([0, 1, 64, 128]), "multi")
        self.assertEqual(build.bank_map([0, 99, 100, 128]), "multi")
        self.assertEqual(build.bank_map([0, 1, 64, 126, 127, 128], "Yamaha XG.sf2"), "xg")
        self.assertEqual(build.bank_map([0, 1, 128], "Kit1.8 XG"), "xg")
        self.assertEqual(build.bank_map([0, 1, 128], "SC-55 24-bit XGD Edition"), "xg")
        self.assertEqual(build.bank_map([0, 1, 128], "GM_GS_XG_SFX"), "xg")
        self.assertEqual(build.bank_map([0, 1, 128], "Gremlin GS"), "gs_var")  # 'xg' needs a boundary
        self.assertEqual(build.bank_map([0, 128], "Yamaha XG.sf2"), "gm")  # pure GM layout stays gm


class SizeBucketTests(unittest.TestCase):
    def test_boundaries(self):
        mib = 1 << 20
        self.assertEqual(build.size_bucket(0), "<2MB")
        self.assertEqual(build.size_bucket(2 * mib - 1), "<2MB")
        self.assertEqual(build.size_bucket(2 * mib), "2-16MB")
        self.assertEqual(build.size_bucket(16 * mib - 1), "2-16MB")
        self.assertEqual(build.size_bucket(16 * mib), "16-100MB")
        self.assertEqual(build.size_bucket(100 * mib), "100MB-1GB")
        self.assertEqual(build.size_bucket(1024 * mib - 1), "100MB-1GB")
        self.assertEqual(build.size_bucket(1024 * mib), ">1GB")
        self.assertEqual(build.size_bucket(5 << 30), ">1GB")


class LineageTests(unittest.TestCase):
    def lin(self, **fields):
        return build.lineage(fields, COMPILED)

    def test_each_lineage(self):
        cases = {
            "console": ["Super Nintendo", "SNES rip", "n64 bank", "Sega Genesis", "gameboy", "PSX", "arcade"],
            "fm_sampled": ["OPL3 FM", "OPL-2_FM", "AdLib Gold", "YM2612", "SB16 FM", "4-Op FM bank"],
            "gravis": ["Gravis Ultrasound", "GUS patches", "FreePats", "eawpats"],
            "roland": ["Roland SC-55", "SC55", "Sound Canvas", "JV-1010", "MT-32", "CM-32L", "GS sound set (16 bit)"],
            "yamaha_xg": ["Yamaha XG", "S-YXG50", "DB50XG", "SW60XG", "MU-100", "xg set"],
            "creative": ["Creative Labs", "Sound Blaster", "AWE32", "AWE 64", "SB Live!", "Audigy", "E-mu", "EMU8000", "8MBGMGS", "CT4MGM", "CTGMGS6MB"],
            "gs_compat": ["GeneralUser GS", "GM_GS_SFX", "general standard", "UltimateGMGS"],
        }
        for expected, names in cases.items():
            for name in names:
                got, why = self.lin(INAM=name)
                self.assertEqual(got, expected, f"{name!r} -> {got} ({why})")

    def test_boundaries_avoid_substrings(self):
        for name in ("people", "Kings GM", "Things", "Emulator", "Lemur", "argus", "Dangerous GM", "n6400"):
            self.assertEqual(self.lin(INAM=name)[0], "generic", name)

    def test_generic_when_nothing_matches(self):
        self.assertEqual(self.lin(INAM="Plain GM", file="Plain.sf2"), ("generic", None))

    def test_field_group_priority(self):
        # name beats copyright beats comment
        got, why = self.lin(INAM="Yamaha XG", ICOP="Roland", ICMT="for AWE32")
        self.assertEqual((got, why), ("yamaha_xg", "INAM:Yamaha"))
        got, why = self.lin(INAM="Plain", ICOP="Roland", ICMT="for AWE32")
        self.assertEqual((got, why), ("roland", "ICOP:Roland"))
        got, why = self.lin(INAM="Plain", ICMT="for AWE32")
        self.assertEqual((got, why), ("creative", "ICMT:AWE32"))

    def test_table_order_within_a_group(self):
        got, _ = self.lin(INAM="SNES Roland")  # console precedes roland
        self.assertEqual(got, "console")
        got, _ = self.lin(INAM="Roland", file="Yamaha.sf2")  # roland precedes yamaha_xg
        self.assertEqual(got, "roland")

    def test_tool_defaults_are_ignored(self):
        self.assertEqual(self.lin(IPRD="SBAWE32")[0], "generic")
        self.assertEqual(self.lin(IPRD="sbawe32 ")[0], "generic")
        self.assertEqual(self.lin(IPRD="SBAWE32 with extra words")[0], "creative")
        self.assertEqual(build.lineage({"IPRD": "SBAWE32"}, COMPILED, ignore_values=())[0], "creative")

    def test_override_table_replaces_defaults(self):
        compiled = build.compile_lineage_regex({"console": "doom", "roland": "roland"})
        self.assertEqual(build.lineage({"INAM": "Doom Roland"}, compiled), ("console", "INAM:Doom"))
        self.assertEqual(build.lineage({"INAM": "Yamaha XG"}, compiled), ("generic", None))


class YearTests(unittest.TestCase):
    def test_icrd_formats(self):
        cases = {
            "Aug 16, 1998": 1998,
            "2014-12-23 13:34:58": 2014,
            "98. 12. 25": 1998,
            "22  7 96": 1996,
            "1-Jan-97": 1997,
            "3/7/19": 2019,
            "04/17/20": 2020,
            "2003-2021": 2021,
            "2000Äê11ÔÂ17ÈÕ": 2000,
            "domingo 17 mayo 2020, 00:40:42": 2020,
            "13  8 94": 1994,
            "2012": 2012,
            "": None,
            "someday": None,
            "1985": None,  # before YEAR_MIN
            "2099": None,  # in the future
        }
        for text, expected in cases.items():
            self.assertEqual(build.year_from_icrd(text, NOW_YEAR), expected, text)

    def test_text_years_take_latest(self):
        self.assertEqual(build.year_from_text("(c) 1996-2003 someone", NOW_YEAR), 2003)
        self.assertEqual(build.year_from_text("v1.0 20000 samples", NOW_YEAR), None)  # 5 digits is not a year
        self.assertEqual(build.year_from_text("", NOW_YEAR), None)

    def test_precedence_and_confidence(self):
        info = {"ICRD": "Jun 10, 1999", "ICMT": "made 2005", "INAM": "Font 2010"}
        self.assertEqual(build.year_and_confidence(info, "x 2020.sf2", NOW_YEAR), (1999, "high", "ICRD"))
        info["ICRD"] = "3/7/19"
        self.assertEqual(build.year_and_confidence(info, "x.sf2", NOW_YEAR), (2019, "medium", "ICRD"))
        info["ICRD"] = ""
        self.assertEqual(build.year_and_confidence(info, "x.sf2", NOW_YEAR), (2005, "medium", "ICMT"))
        info["ICMT"] = None
        self.assertEqual(build.year_and_confidence(info, "x.sf2", NOW_YEAR), (2010, "medium", "INAM"))
        info["INAM"] = "Font"
        self.assertEqual(build.year_and_confidence(info, "Orpheus_18.06.2020.sf2", NOW_YEAR), (2020, "low", "file"))
        self.assertEqual(build.year_and_confidence(info, "x.sf2", NOW_YEAR), (None, "unknown", None))

    def test_decade(self):
        self.assertEqual(build.decade_of(1996), "1990s")
        self.assertEqual(build.decade_of(2000), "2000s")
        self.assertEqual(build.decade_of(2019), "2010s")
        self.assertEqual(build.decade_of(None), "unknown")


class LicenseTests(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(build.license_flag({"ICOP": "Copyright 1996 Roland Corporation U.S."}), ("roland_copyright", "ICOP:Roland"))
        self.assertEqual(build.license_flag({"ICMT": "...based on roland sc-55..."})[0], "roland_copyright")
        self.assertEqual(build.license_flag({"ICOP": "GNU GPL v2"})[0], "gpl")
        self.assertEqual(build.license_flag({"ICOP": "LGPL"})[0], "gpl")
        self.assertEqual(build.license_flag({"ICMT": "General Public License"})[0], "gpl")
        self.assertEqual(build.license_flag({"ICOP": "Public Domain"})[0], "public_domain")
        self.assertEqual(build.license_flag({"ICOP": "CC0 1.0"})[0], "public_domain")
        self.assertEqual(build.license_flag({"ICOP": "Free to use."})[0], "free")
        self.assertEqual(build.license_flag({"ICOP": "Freeware"})[0], "free")
        self.assertEqual(build.license_flag({"ICMT": "Creative Commons Attribution"})[0], "free")
        self.assertEqual(build.license_flag({"ICOP": "CC-BY 3.0"})[0], "free")
        self.assertEqual(build.license_flag({"ICOP": "(c) 2001 Somebody"}), ("unknown", None))
        self.assertEqual(build.license_flag({"INAM": "Freedom Bank"}), ("unknown", None))  # 'free' needs a boundary
        # precedence: roland beats gpl beats pd beats free
        self.assertEqual(build.license_flag({"ICOP": "GPL", "ICMT": "Roland samples"})[0], "roland_copyright")
        self.assertEqual(build.license_flag({"ICOP": "GPL, free public domain"})[0], "gpl")
        self.assertEqual(build.license_flag({"ICOP": "free, public domain"})[0], "public_domain")


class BuildTests(unittest.TestCase):
    def scan(self):
        fonts = [
            font("Zeta GM.sf2", sha="a" * 64, INAM="Zeta", ICRD="Aug 16, 1998", ICOP="Free to use."),
            font("Alpha GM.sf2", sha="a" * 64, INAM="Zeta", ICRD="Aug 16, 1998", ICOP="Free to use."),  # twin
            font("Drums_Kit.sf2", sha="b" * 64, melodic=0, banks=[128], nbytes=100, INAM="Kit"),
            font("Piano_Grand.sf2", sha="c" * 64, melodic=1, drums=False, banks=[0], INAM="Grand", IPRD="SBAWE32"),
            font("SCC1T2.sf2", sha="d" * 64, banks=[0, 1, 8, 128], INAM="GS sound set (16 bit)", ICOP="Copyright 1996 Roland Corporation U.S."),
            font("Nokia.sf2", sha="e" * 64, banks=[0, 121, 128], nbytes=(2 << 30), INAM="Nokia 3510", ICMT="Yamaha XG samples 2004"),
            font("Partial.sf2", sha="f" * 64, melodic=40, drums=True, INAM="Half"),
        ]
        broken = font("Broken.sf2", sha="0" * 64, melodic=0, drums=False, banks=[])
        broken.update({"parse_ok": False, "error": "not a RIFF file"})
        fonts.append(broken)
        return {"schema": 1, "generated_at": "2026-01-01T00:00:00Z", "root": "soundfonts", "count": len(fonts), "fonts": fonts}

    def test_facets_and_counts(self):
        doc = build.build(self.scan(), {"lineage_regex": None, "fonts": {}}, now=NOW)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["count"], 8)
        self.assertEqual(doc["canonical_count"], 7)
        self.assertEqual(doc["parse_failures"], 1)
        self.assertEqual([f["file"] for f in doc["fonts"]], sorted(f["file"] for f in doc["fonts"]))
        by = {f["file"]: f for f in doc["fonts"]}

        z = by["Zeta GM.sf2"]
        self.assertEqual(z["facets"], {
            "completeness": "full_gm", "bank_map": "gm", "size_bucket": "2-16MB", "lineage": "generic",
            "decade": "1990s", "year_confidence": "high", "license_flag": "free",
        })
        self.assertEqual(z["variant_id"], "sf2-aaaaaaaaaa")
        self.assertEqual(z["label"], "Zeta GM")  # label = file stem (INAM is often a tool default)
        self.assertTrue(z["publish"])
        self.assertEqual(z["year"], 1998)
        # duplicates: canonical = first file name in sorted order
        self.assertEqual(z["dup_of"], "Alpha GM.sf2")
        self.assertEqual(z["alias_of"], "sf2-aaaaaaaaaa")
        self.assertIsNone(by["Alpha GM.sf2"]["dup_of"])
        self.assertEqual(doc["duplicates"], [{"sha256": "a" * 64, "canonical": "Alpha GM.sf2", "twins": ["Zeta GM.sf2"]}])

        d = by["Drums_Kit.sf2"]
        self.assertEqual(d["facets"]["completeness"], "single_instrument")
        self.assertEqual(d["instrument"], "drums")
        self.assertEqual(d["completeness_structural"], "drums_only")
        self.assertEqual(d["facets"]["bank_map"], "drums_only")
        self.assertEqual(d["facets"]["size_bucket"], "<2MB")
        self.assertEqual(d["sources"]["completeness"], "filename")

        p = by["Piano_Grand.sf2"]
        self.assertEqual(p["facets"]["completeness"], "single_instrument")
        self.assertEqual(p["instrument"], "piano")
        self.assertEqual(p["facets"]["bank_map"], "melodic_only")
        self.assertEqual(p["facets"]["lineage"], "generic")  # IPRD SBAWE32 ignored

        s = by["SCC1T2.sf2"]
        self.assertEqual(s["facets"]["lineage"], "roland")
        self.assertEqual(s["sources"]["lineage"], "regex:INAM:GS sound set")
        self.assertEqual(s["facets"]["license_flag"], "roland_copyright")
        self.assertEqual(s["facets"]["bank_map"], "gs_var")
        self.assertTrue(s["publish"])  # D2: Roland-ICOP fonts publish by default

        n = by["Nokia.sf2"]
        self.assertEqual(n["facets"]["bank_map"], "gm2")
        self.assertEqual(n["facets"]["size_bucket"], ">1GB")
        self.assertEqual(n["facets"]["lineage"], "yamaha_xg")
        self.assertEqual(n["sources"]["lineage"], "regex:ICMT:Yamaha")
        self.assertEqual((n["year"], n["facets"]["decade"], n["facets"]["year_confidence"]), (2004, "2000s", "medium"))

        self.assertEqual(by["Partial.sf2"]["facets"]["completeness"], "partial")

        b = by["Broken.sf2"]
        self.assertFalse(b["parse_ok"])
        self.assertFalse(b["publish"])  # never publish what we could not parse
        self.assertEqual(b["facets"]["completeness"], "partial")

        self.assertEqual(doc["counts"]["completeness"], {"full_gm": 4, "partial": 2, "single_instrument": 2})
        self.assertEqual(doc["counts"]["instrument"], {"drums": 1, "piano": 1})
        self.assertEqual(doc["counts"]["publish"], {"false": 1, "true": 7})
        self.assertEqual(doc["counts"]["lineage"]["roland"], 1)
        self.assertEqual(sum(doc["counts"]["size_bucket"].values()), 8)
        self.assertEqual(doc["unused_override_keys"], [])
        self.assertEqual(doc["lineage_regex"], build.DEFAULT_LINEAGE_REGEX)

    def test_overrides(self):
        overrides = {
            "lineage_regex": None,
            "fonts": {
                "d" * 64: {"file": "SCC1T2.sf2", "lineage": "roland", "notes": "pre-seed"},
                "a" * 64: {"publish": False, "label": "Shared label"},
                "Zeta GM.sf2": {"label": "Zeta only", "year": 2001, "license_flag": "gpl"},
                "Nokia.sf2": {"decade": "2010s", "completeness": "partial", "bank_map": "multi", "instrument": "bass"},
                "Nobody.sf2": {"publish": False},
            },
        }
        doc = build.build(self.scan(), overrides, now=NOW)
        by = {f["file"]: f for f in doc["fonts"]}
        s = by["SCC1T2.sf2"]
        self.assertEqual(s["facets"]["lineage"], "roland")
        self.assertEqual(s["sources"]["lineage"], "override")
        self.assertEqual(s["notes"], "pre-seed")
        self.assertEqual(s["overrides_applied"], ["lineage", "notes"])
        # sha entry applies to both twins; filename entry layers on top for one of them
        self.assertFalse(by["Alpha GM.sf2"]["publish"])
        self.assertEqual(by["Alpha GM.sf2"]["label"], "Shared label")
        self.assertFalse(by["Zeta GM.sf2"]["publish"])
        self.assertEqual(by["Zeta GM.sf2"]["label"], "Zeta only")
        self.assertEqual(by["Zeta GM.sf2"]["year"], 2001)
        self.assertEqual(by["Zeta GM.sf2"]["facets"]["decade"], "2000s")
        self.assertEqual(by["Zeta GM.sf2"]["facets"]["year_confidence"], "high")
        self.assertEqual(by["Zeta GM.sf2"]["facets"]["license_flag"], "gpl")
        self.assertEqual(by["Zeta GM.sf2"]["sources"]["license_flag"], "override")
        n = by["Nokia.sf2"]
        self.assertEqual(n["facets"]["decade"], "2010s")
        self.assertEqual(n["facets"]["completeness"], "partial")
        self.assertEqual(n["facets"]["bank_map"], "multi")
        self.assertEqual(n["instrument"], "bass")
        self.assertEqual(doc["unused_override_keys"], ["Nobody.sf2"])
        self.assertEqual(doc["counts"]["publish"], {"false": 3, "true": 5})

    def test_lineage_regex_override(self):
        overrides = {"lineage_regex": {"console": "nokia", "roland": "roland"}, "fonts": {}}
        doc = build.build(self.scan(), overrides, now=NOW)
        by = {f["file"]: f for f in doc["fonts"]}
        self.assertEqual(by["Nokia.sf2"]["facets"]["lineage"], "console")
        self.assertEqual(by["SCC1T2.sf2"]["facets"]["lineage"], "roland")
        self.assertEqual(doc["lineage_regex"], overrides["lineage_regex"])

    def test_load_overrides_validates_keys(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "o.json")
            with open(path, "w") as fh:
                json.dump({"_comment": "x", "fonts": {"_examples": {"bogus": 1}, "x.sf2": {"publish": True, "file": "x.sf2"}}}, fh)
            ov = build.load_overrides(path)
            self.assertEqual(list(ov["fonts"]), ["x.sf2"])
            with open(path, "w") as fh:
                json.dump({"fonts": {"x.sf2": {"colour": "blue"}}}, fh)
            with self.assertRaises(ValueError):
                build.load_overrides(path)
            self.assertEqual(build.load_overrides(os.path.join(td, "missing.json"))["fonts"], {})

    def test_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            scan_path = os.path.join(td, "scan.json")
            out = os.path.join(td, "facets.json")
            with open(scan_path, "w") as fh:
                json.dump(self.scan(), fh)
            rc = build.main(["--scan", scan_path, "--overrides", os.path.join(td, "none.json"), "--out", out, "--quiet"])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["count"], 8)
            self.assertEqual(doc["scan_generated_at"], "2026-01-01T00:00:00Z")


class RepoOverridesTests(unittest.TestCase):
    """The committed overrides.json must always load and only use known keys."""

    def test_committed_overrides_load(self):
        path = os.path.join(os.path.dirname(__file__), "..", "overrides.json")
        ov = build.load_overrides(path)
        self.assertGreaterEqual(len(ov["fonts"]), 5)
        seeds = [e for e in ov["fonts"].values() if e.get("lineage") == "roland" and "GS sound set" in (e.get("notes") or "")]
        self.assertEqual(len(seeds), 5)


if __name__ == "__main__":
    unittest.main()
