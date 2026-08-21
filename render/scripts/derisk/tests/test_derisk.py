"""Unit tests for the M0b de-risking helpers (stdlib unittest; no container needed).

    python3 -m unittest discover -s render/scripts/derisk/tests -t render/scripts/derisk -v
"""
import array
import math
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import midi_end  # noqa: E402
import seam_test  # noqa: E402


def vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.insert(0, 0x80 | (n & 0x7F))
        n >>= 7
    return bytes(out)


def smf(tracks, ppqn=96, fmt=1):
    body = b"".join(b"MTrk" + struct.pack(">I", len(t)) + t for t in tracks)
    return b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), ppqn) + body


class MidiEndTest(unittest.TestCase):
    def write(self, data):
        fd, path = tempfile.mkstemp(suffix=".mid")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        self.addCleanup(os.unlink, path)
        return path

    def test_default_tempo_and_running_status(self):
        # 120 bpm default, ppqn 96: note-on at 0, note-off (running status) at 4 beats = 2.0 s, EOT 1 beat later
        trk = (vlq(0) + b"\x90\x3c\x40" + vlq(384) + b"\x3c\x00" + vlq(96) + b"\xff\x2f\x00")
        r = midi_end.parse(self.write(smf([trk])))
        self.assertAlmostEqual(r["last_note_s"], 2.0, places=6)
        self.assertAlmostEqual(r["last_event_s"], 2.5, places=6)
        self.assertEqual(r["D_s"], 6)  # ceil((2+3)/2)*2

    def test_tempo_change_across_tracks(self):
        tempo = vlq(0) + b"\xff\x51\x03" + (1000000).to_bytes(3, "big") + vlq(0) + b"\xff\x2f\x00"  # 60 bpm at tick 0
        notes = vlq(0) + b"\x90\x40\x40" + vlq(96 * 10) + b"\x80\x40\x00" + vlq(0) + b"\xff\x2f\x00"  # off at 10 beats = 10 s
        r = midi_end.parse(self.write(smf([tempo, notes])))
        self.assertAlmostEqual(r["last_note_s"], 10.0, places=6)
        self.assertEqual(r["D_s"], 14)
        self.assertEqual(r["tempo_changes"], 1)

    def test_D_rounding_up_to_even(self):
        # last note at 3.0 s -> (3+3)/2 = 3 -> D = 6 ; last note at 3.01 s -> D = 8
        trk = vlq(0) + b"\x90\x3c\x40" + vlq(576) + b"\x80\x3c\x00" + vlq(0) + b"\xff\x2f\x00"
        self.assertEqual(midi_end.parse(self.write(smf([trk])))["D_s"], 6)
        trk = vlq(0) + b"\x90\x3c\x40" + vlq(578) + b"\x80\x3c\x00" + vlq(0) + b"\xff\x2f\x00"
        self.assertEqual(midi_end.parse(self.write(smf([trk])))["D_s"], 8)

    def test_rejects_non_midi(self):
        with self.assertRaises(ValueError):
            midi_end.parse(self.write(b"RIFF....WAVE"))


class SeamMathTest(unittest.TestCase):
    def test_constants_match_plan(self):
        self.assertEqual(seam_test.SEG, 102720)
        self.assertEqual(seam_test.HOP, 96000)
        self.assertEqual(seam_test.LEAD_IN, 5760)
        self.assertEqual(seam_test.SEG - seam_test.HOP, 6720)  # overlap = 0.14 s

    def test_nsr_identical_and_offset(self):
        ref = array.array("f", [0.5, -0.5] * 100)
        self.assertEqual(seam_test.nsr(ref, ref)["nsr_db"], None if sum(x * x for x in ref) == 0 else seam_test.nsr(ref, ref)["nsr_db"])
        self.assertEqual(seam_test.nsr(ref, ref)["err_dbfs"], float("-inf"))
        sig = array.array("f", [x + 0.05 for x in ref])  # error 0.05 on 0.5 -> -20 dB
        self.assertAlmostEqual(seam_test.nsr(sig, ref)["nsr_db"], -20.0, places=2)

    def test_max_step_is_per_channel(self):
        # L: 0, 1, 0 (step 1.0); R: 0, 0, 0 -> interleaved
        sig = array.array("f", [0.0, 0.0, 1.0, 0.0, 0.0, 0.0])
        self.assertAlmostEqual(seam_test.max_step(sig), 1.0)
        sig = array.array("f", [0.0, 0.0, 0.0, 0.25, 0.0, 0.25])
        self.assertAlmostEqual(seam_test.max_step(sig), 0.25)

    def test_db_helpers(self):
        self.assertAlmostEqual(seam_test.db(0.1), -10.0)
        self.assertAlmostEqual(seam_test.dba(0.01), -40.0)
        self.assertEqual(seam_test.dba(0.0), float("-inf"))

    def test_best_lag_detects_shift(self):
        n = 3000
        ref = array.array("f", [0.0] * (2 * n))
        for i in range(n):
            ref[2 * i] = math.sin(i * 0.37) + 0.3 * math.sin(i * 1.13)
        for shift in (0, 3, -7):
            sig = array.array("f", [0.0] * (2 * n))
            for i in range(n):
                j = i - shift
                if 0 <= j < n:
                    sig[2 * i] = ref[2 * j]
            # sig[(i+lag)] == ref[i]  <=>  lag == shift
            self.assertEqual(seam_test.best_lag(sig, ref, 500, 1500, 20), shift)


if __name__ == "__main__":
    unittest.main()
