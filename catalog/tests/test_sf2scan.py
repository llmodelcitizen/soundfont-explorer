import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
from unittest import mock

from catalog import sf2scan
from catalog.tests.fixtures import build_sf2, chunk, phdr_record, sdta_range

PRESETS = (
    ("Piano", 0, 0),
    ("Bright Piano", 1, 0),
    ("Strings", 48, 0),
    ("Piano again", 0, 0),  # duplicate (bank, preset): counted once in melodic_bank0
    ("Warm Pad var", 89, 1),
    ("Standard Kit", 0, 128),
    ("Room Kit", 8, 128),
)


class RecordingFile(io.BytesIO):
    """BytesIO that records every (start, end) byte range read."""

    def __init__(self, data):
        super().__init__(data)
        self.reads = []

    def read(self, n=-1):
        start = self.tell()
        out = super().read(n)
        self.reads.append((start, start + len(out)))
        return out


class ParseTests(unittest.TestCase):
    def scan_bytes(self, data, name="fixture.sf2"):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, name)
            with open(path, "wb") as fh:
                fh.write(data)
            return sf2scan.scan_file(path, name)

    def test_parses_info_and_presets(self):
        rec = self.scan_bytes(build_sf2(presets=PRESETS, info={"INAM": "Odd", "ICMT": "Even!"}))
        self.assertTrue(rec["parse_ok"], rec["error"])
        self.assertIsNone(rec["error"])
        self.assertEqual(rec["ifil"], "2.1")
        self.assertEqual(rec["info"]["INAM"], "Odd")
        self.assertEqual(rec["info"]["ICMT"], "Even!")
        self.assertEqual(rec["info"]["ICRD"], "Aug 16, 1998")
        self.assertEqual(rec["info"]["IENG"], "Tester")
        self.assertEqual(rec["info"]["IPRD"], "SBAWE32")
        self.assertEqual(rec["info"]["ICOP"], "Copyright 1998 Tester")
        self.assertEqual(rec["info"]["ISFT"], "handmade")
        self.assertEqual(rec["info"]["isng"], "EMU8000")
        self.assertEqual(rec["preset_count"], len(PRESETS))  # EOP dropped
        self.assertEqual(rec["melodic_bank0"], 3)  # presets 0, 1, 48 (0 counted once)
        self.assertTrue(rec["has_drums"])
        self.assertEqual(rec["banks"], [0, 1, 128])
        self.assertEqual(rec["presets_by_bank"], {"0": 4, "1": 1, "128": 2})
        self.assertEqual(rec["warnings"], [])

    def test_odd_sized_chunks_are_padded(self):
        # "Odd" + NUL = 4 bytes (even); force odd lengths on several strings so the
        # walker must honour the pad byte to find the following chunks.
        info = {"INAM": b"Odd\0\0", "ICRD": b"1999\0"}
        self.assertEqual(len(info["INAM"]) % 2, 1)
        self.assertEqual(len(info["ICRD"]) % 2, 1)
        rec = self.scan_bytes(build_sf2(presets=PRESETS, info=info))
        self.assertTrue(rec["parse_ok"], rec["error"])
        self.assertEqual(rec["info"]["INAM"], "Odd")
        self.assertEqual(rec["info"]["ICRD"], "1999")
        self.assertEqual(rec["info"]["ISFT"], "handmade")  # chunks after the odd ones still parse

    def test_odd_sized_chunk_before_phdr_is_skipped(self):
        rec = self.scan_bytes(build_sf2(presets=PRESETS, pdta_prefix=chunk("junk", b"\x01\x02\x03")))
        self.assertTrue(rec["parse_ok"], rec["error"])
        self.assertEqual(rec["preset_count"], len(PRESETS))

    def test_eop_is_dropped_by_position_not_name(self):
        presets = (("EOP", 5, 0), ("Real", 6, 0))  # a preset legitimately named EOP stays
        rec = self.scan_bytes(build_sf2(presets=presets))
        self.assertEqual(rec["preset_count"], 2)
        self.assertEqual(rec["melodic_bank0"], 2)
        self.assertFalse(rec["has_drums"])
        self.assertEqual(rec["banks"], [0])

    def test_only_eop_means_zero_presets(self):
        rec = self.scan_bytes(build_sf2(presets=()))
        self.assertTrue(rec["parse_ok"])
        self.assertEqual(rec["preset_count"], 0)
        self.assertEqual(rec["banks"], [])
        self.assertEqual(rec["presets_by_bank"], {})

    def test_sdta_payload_is_never_read(self):
        data = build_sf2(presets=PRESETS, sdta_bytes=4096)
        start, end = sdta_range(data)
        fh = RecordingFile(data)
        walked = sf2scan._walk_riff(fh, len(data))
        self.assertEqual(len(sf2scan.parse_phdr(walked["phdr"])), len(PRESETS))
        for r_start, r_end in fh.reads:
            self.assertFalse(r_start < end and r_end > start, f"read {r_start}-{r_end} overlaps sdta {start}-{end}")

    def test_latin1_fallback_and_nul_stripping(self):
        raw_name = b"Caf\xe9 \x00garbage after nul"
        rec = self.scan_bytes(build_sf2(info={"INAM": raw_name}))
        self.assertTrue(rec["parse_ok"], rec["error"])
        self.assertEqual(rec["info"]["INAM"], "Café")

    def test_utf8_decodes(self):
        rec = self.scan_bytes(build_sf2(info={"INAM": "Fönt ☃".encode("utf-8") + b"\0"}))
        self.assertEqual(rec["info"]["INAM"], "Fönt ☃")

    def test_missing_info_fields_are_null(self):
        rec = self.scan_bytes(build_sf2(info={"ICMT": None, "ISFT": None, "IPRD": None}))
        self.assertTrue(rec["parse_ok"])
        self.assertIsNone(rec["info"]["ICMT"])
        self.assertIsNone(rec["info"]["ISFT"])
        self.assertEqual(set(rec["info"]), set(sf2scan.INFO_KEYS))

    def test_riff_size_mismatch_is_a_warning_not_an_error(self):
        rec = self.scan_bytes(build_sf2(presets=PRESETS, riff_size_override=10))
        self.assertTrue(rec["parse_ok"], rec["error"])
        self.assertEqual(len(rec["warnings"]), 1)
        self.assertIn("RIFF size field", rec["warnings"][0])

    def test_not_riff(self):
        rec = self.scan_bytes(b"\x00" * 100)
        self.assertFalse(rec["parse_ok"])
        self.assertIn("not a RIFF", rec["error"])
        self.assertEqual(rec["bytes"], 100)
        self.assertEqual(rec["banks"], [])

    def test_wrong_form_type(self):
        data = bytearray(build_sf2())
        data[8:12] = b"WAVE"
        rec = self.scan_bytes(bytes(data))
        self.assertFalse(rec["parse_ok"])
        self.assertIn("WAVE", rec["error"])

    def test_truncated_file(self):
        data = build_sf2(presets=PRESETS)
        rec = self.scan_bytes(data[: len(data) // 2 + 40])  # cut inside pdta
        self.assertFalse(rec["parse_ok"])
        self.assertTrue(rec["error"])

    def test_tiny_file(self):
        rec = self.scan_bytes(b"RIFF")
        self.assertFalse(rec["parse_ok"])
        self.assertIn("truncated", rec["error"])

    def test_phdr_bad_size(self):
        with self.assertRaises(sf2scan.SF2Error):
            sf2scan.parse_phdr(phdr_record("X", 0, 0) + b"\0" * 5)

    def test_phdr_missing_eop(self):
        with self.assertRaises(sf2scan.SF2Error):
            sf2scan.parse_phdr(b"")

    def test_info_cap(self):
        big = b"x" * (sf2scan.INFO_CAP + 100)
        rec = self.scan_bytes(build_sf2(presets=PRESETS, info={"ICMT": big + b"\0"}))
        self.assertTrue(rec["parse_ok"], rec["error"])
        self.assertTrue(any("parsed only the first" in w for w in rec["warnings"]))
        self.assertEqual(rec["preset_count"], len(PRESETS))

    def test_list_smaller_than_its_type_is_a_parse_failure(self):
        """LIST/INFO size 0..3: `size - 4` went negative, so fh.read(-1) slurped the whole file
        (size 3) or fh.read(-2..-4) raised ValueError out of a "never raises" function."""
        for size in (0, 1, 2, 3):
            data = bytearray(build_sf2(presets=PRESETS))
            struct.pack_into("<I", data, 16, size)   # first top-level chunk = LIST/INFO
            rec = self.scan_bytes(bytes(data))
            self.assertFalse(rec["parse_ok"], size)
            self.assertIn("LIST chunk", rec["error"])

    def test_phdr_size_is_bounded_by_the_file_and_the_format(self):
        class RequestRecorder(io.BytesIO):
            def __init__(self, data):
                super().__init__(data)
                self.requested = []

            def read(self, n=-1):
                self.requested.append(n)
                return super().read(n)

        data = bytearray(build_sf2(presets=PRESETS))
        # LIST/pdta and its phdr both claim ~4 GiB: neither may turn into a 4 GiB read
        pos = 12
        while pos + 12 <= len(data):
            (size,) = struct.unpack_from("<I", data, pos + 4)
            if data[pos:pos + 4] == b"LIST" and data[pos + 8:pos + 12] == b"pdta":
                struct.pack_into("<I", data, pos + 4, 0xFFFFFFFF)
                struct.pack_into("<I", data, pos + 16, 0xFFFFFFF0)   # phdr is the first sub-chunk
                break
            pos += 8 + size + (size & 1)
        else:
            self.fail("no pdta in fixture")
        fh = RequestRecorder(bytes(data))
        walked = sf2scan._walk_riff(fh, len(data))
        self.assertLessEqual(max(fh.requested), len(data))   # never asks for more than the file holds
        self.assertTrue(any("overruns" in w for w in walked["warnings"]))
        self.scan_bytes(bytes(data))                          # and scan_file still never raises
        # a phdr bigger than 65536 records cannot be a SoundFont (bag indices are u16)
        hdr = b"phdr" + struct.pack("<I", sf2scan.PHDR_CAP + sf2scan.PHDR_RECORD) + b"\0" * 8
        with self.assertRaises(sf2scan.SF2Error) as cm:
            sf2scan._find_phdr(io.BytesIO(hdr), 0, 1 << 40, [])
        self.assertIn("records", str(cm.exception))


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.files = {
            "b.sf2": build_sf2(presets=PRESETS, info={"INAM": "B"}),
            "A.SF2": build_sf2(presets=PRESETS[:3], info={"INAM": "A"}),
            "c.sf2": build_sf2(presets=PRESETS, info={"INAM": "B"}),  # byte-identical twin of b.sf2
            "broken.sf2": b"RIFF\x10\x00\x00\x00sfbkjunk",
            "notes.txt": b"ignore me",
            "archive.zip": b"PK\x03\x04 ignore me",
        }
        for name, data in self.files.items():
            with open(os.path.join(self.root, name), "wb") as fh:
                fh.write(data)
        os.mkdir(os.path.join(self.root, "dir.sf2"))  # a directory with the extension is ignored

    def tearDown(self):
        self.td.cleanup()

    def test_listing_is_case_insensitive_and_sorted(self):
        self.assertEqual(sf2scan.list_soundfonts(self.root), ["A.SF2", "b.sf2", "broken.sf2", "c.sf2"])

    def test_scan_dir_hashes_and_reports_failures(self):
        doc = sf2scan.scan_dir(self.root, threads=2)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["count"], 4)
        self.assertEqual(doc["parse_failures"], 1)
        self.assertEqual([f["file"] for f in doc["fonts"]], ["A.SF2", "b.sf2", "broken.sf2", "c.sf2"])
        by = {f["file"]: f for f in doc["fonts"]}
        self.assertEqual(by["b.sf2"]["sha256"], hashlib.sha256(self.files["b.sf2"]).hexdigest())
        self.assertEqual(by["b.sf2"]["sha256"], by["c.sf2"]["sha256"])
        self.assertNotEqual(by["b.sf2"]["sha256"], by["A.SF2"]["sha256"])
        self.assertFalse(by["broken.sf2"]["parse_ok"])
        self.assertTrue(by["broken.sf2"]["error"])
        self.assertIsNotNone(by["broken.sf2"]["sha256"])  # still hashed
        self.assertEqual(by["b.sf2"]["bytes"], len(self.files["b.sf2"]))

    def test_limit_and_no_hash(self):
        doc = sf2scan.scan_dir(self.root, limit=2, do_hash=False)
        self.assertEqual([f["file"] for f in doc["fonts"]], ["A.SF2", "b.sf2"])
        self.assertTrue(all(f["sha256"] is None for f in doc["fonts"]))

    def test_cli_writes_json(self):
        out = os.path.join(self.root, "out.json")
        rc = sf2scan.main(["--root", self.root, "--out", out, "--limit", "2", "--quiet", "--threads", "2"])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            doc = json.load(fh)
        self.assertEqual(doc["count"], 2)
        self.assertEqual(doc["root"], os.path.normpath(self.root))
        rc = sf2scan.main(["--root", self.root, "--out", out, "--quiet"])
        self.assertEqual(rc, 1)  # broken.sf2 -> non-zero exit, output still written
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["parse_failures"], 1)

    def test_cli_bad_root(self):
        self.assertEqual(sf2scan.main(["--root", os.path.join(self.root, "nope"), "--quiet"]), 2)

    def test_non_utf8_file_name_fails_before_hashing(self):
        """os.listdir returns a non-UTF-8 name as lone surrogates; json.dump(ensure_ascii=False)
        cannot write those, and it used to find out in write_json — after the sha256 pass over
        the whole collection. Refuse up front, naming the file."""
        with open(os.path.join(os.fsencode(self.root), b"caf\xe9.sf2"), "wb") as fh:
            fh.write(self.files["b.sf2"])
        with self.assertRaises(ValueError) as cm:
            sf2scan.list_soundfonts(self.root)
        self.assertIn("caf�.sf2", str(cm.exception))
        self.assertIn("rename", str(cm.exception))
        out = os.path.join(self.root, "out.json")
        with mock.patch.object(sf2scan, "sha256_file", side_effect=AssertionError("hashed anyway")):
            self.assertEqual(sf2scan.main(["--root", self.root, "--out", out, "--quiet"]), 2)
        self.assertFalse(os.path.exists(out))


class StructTests(unittest.TestCase):
    def test_phdr_record_layout(self):
        self.assertEqual(sf2scan.PHDR_RECORD, 38)
        self.assertEqual(struct.calcsize("<20sHHHIII"), 38)


if __name__ == "__main__":
    unittest.main()
