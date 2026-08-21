import os
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mkpack  # noqa: E402


class TestSFPK(unittest.TestCase):
    def test_roundtrip_layout(self):
        members = [b"OggS" + bytes([i]) * (100 + 37 * i) for i in range(24)]
        pack = mkpack.build_pack(members)
        self.assertEqual(pack[:4], b"SFPK")
        self.assertEqual(pack[4], 1)
        self.assertEqual(pack[5], 24)
        self.assertEqual(struct.unpack("<H", pack[6:8])[0], 0)
        self.assertEqual(len(pack), 8 + 4 * 24 + sum(len(m) for m in members))
        self.assertEqual(mkpack.parse_pack(pack), members)
        # member 5 starts at 8 + 4n + sum(len[0..5))
        n, lens, offsets = mkpack.parse_header(pack[:8 + 4 * 24])
        self.assertEqual(offsets[5], 8 + 96 + sum(len(m) for m in members[:5]))
        s, e = mkpack.member_range(pack[:104], 5)
        self.assertEqual(pack[s:e + 1], members[5])

    def test_header_only_parse_and_truncation(self):
        pack = mkpack.build_pack([b"a", b"bb", b"ccc"])
        n, lens, offsets = mkpack.parse_header(pack[:20])
        self.assertEqual((n, lens, offsets), (3, [1, 2, 3], [20, 21, 23]))
        with self.assertRaises(ValueError):
            mkpack.parse_header(pack[:12])
        with self.assertRaises(ValueError):
            mkpack.parse_pack(pack[:-1])
        with self.assertRaises(ValueError):
            mkpack.parse_pack(b"NOPE" + pack[4:])

    def test_count_bounds(self):
        with self.assertRaises(ValueError):
            mkpack.build_pack([])
        with self.assertRaises(ValueError):
            mkpack.build_pack([b"x"] * 256)
        self.assertEqual(len(mkpack.parse_pack(mkpack.build_pack([b"x"] * 255))), 255)

    def test_cli_fill_and_info(self):
        with tempfile.TemporaryDirectory() as td:
            vdirs = []
            for v in ("A", "B"):
                d = os.path.join(td, v, "seg")
                os.makedirs(d)
                with open(os.path.join(d, "0003.opus"), "wb") as f:
                    f.write(("OggS-%s" % v).encode() * 10)
                vdirs.append(os.path.join(td, v))
            out = os.path.join(td, "0003.pk")
            r = subprocess.run([sys.executable, mkpack.__file__, "--slice", "3", "--variants", *vdirs,
                                "--fill", "24", "--out", out], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out, "rb") as f:
                members = mkpack.parse_pack(f.read())
            self.assertEqual(len(members), 24)
            self.assertEqual(members[0], b"OggS-A" * 10)
            self.assertEqual(members[1], b"OggS-B" * 10)
            self.assertEqual(members[2], b"OggS-A" * 10)
            r = subprocess.run([sys.executable, mkpack.__file__, "--info", out], capture_output=True, text=True)
            self.assertIn('"count": 24', r.stdout)
            self.assertIn('"consistent": true', r.stdout)


if __name__ == "__main__":
    unittest.main()
