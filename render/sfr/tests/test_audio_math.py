import unittest
from pathlib import Path

from sfr.config import RenderSettings
from sfr.encode import BYTES_PER_FRAME, opusenc_argv, padded_length_samples, slice_bytes
from sfr.loudness import gain_db, is_silent, master_filter, parse_loudnorm
from sfr.ogg import OggError, opus_info
from sfr.sched import JobError

S = RenderSettings()
FIX = Path(__file__).parent / "fixtures"


class TestLoudness(unittest.TestCase):
    def test_parse(self):
        txt = 'junk\n[Parsed_loudnorm_0 @ 0x1] \n{\n\t"input_i" : "-21.30",\n\t"input_tp" : "-3.20",\n\t"input_lra" : "9.0",\n\t"input_thresh" : "-31.3",\n\t"output_i" : "-16.0"\n}\n'
        d = parse_loudnorm(txt)
        self.assertAlmostEqual(d["input_i"], -21.3)
        self.assertAlmostEqual(d["input_tp"], -3.2)
        self.assertEqual(parse_loudnorm(txt.replace('"-21.30"', '"-inf"'))["input_i"], float("-inf"))
        with self.assertRaises(JobError):
            parse_loudnorm("nothing here")

    def test_gain(self):
        self.assertAlmostEqual(gain_db(-21.3, -3.2, S), 1.7)        # TP-limited: -1.5 - -3.2 = 1.7 < 5.3
        self.assertAlmostEqual(gain_db(-21.3, -9.0, S), 5.3)        # loudness-limited
        self.assertAlmostEqual(gain_db(-60.0, -50.0, S), 30.0)      # clamp
        self.assertAlmostEqual(gain_db(10.0, 2.0, S), -26.0)
        self.assertAlmostEqual(gain_db(30.0, 20.0, S), -30.0)
        self.assertTrue(is_silent(-51, S))
        self.assertTrue(is_silent(float("-inf"), S))
        self.assertTrue(is_silent(float("nan"), S))
        self.assertFalse(is_silent(-49.9, S))

    def test_master_filter(self):
        af = master_filter(5.3, S, 188)
        self.assertIn("volume=5.3000dB", af)
        self.assertIn("aresample=48000:resampler=soxr:precision=28", af)
        self.assertIn("atrim=end_sample=9024000", af)
        self.assertIn("apad=whole_len=9024000", af)
        self.assertNotIn("adelay", af)
        self.assertNotIn("start_sample", af)
        self.assertNotIn("asetrate", af)
        # engine lags by 10 ms → trim 480 samples
        self.assertIn("atrim=start_sample=480", master_filter(0, S, 10, start_offset_s=0.010))
        # engine leads by 10 ms → delay
        self.assertIn("adelay=480S|480S", master_filter(0, S, 10, start_offset_s=-0.010))
        # drift: asetrate before resample
        af = master_filter(0, S, 10, drift_ppm=12.5, native_rate=44100)
        # 10× intermediate rate → asetrate resolution ≈ 2.3 ppm (441000 × (1 + 12.5e-6) = 441005.5)
        self.assertIn("aresample=441000:resampler=soxr:precision=28,asetrate=441006,aresample=48000", af)


class TestEncodeMath(unittest.TestCase):
    def test_padded_length_covers_both_tiers(self):
        for D in (2, 4, 10, 12, 100, 186, 188, 190, 252, 360):
            total = padded_length_samples(S, D)
            n, m = S.n_slices(D), S.n_listen(D)
            last_scrub_end = (n - 1) * S.slice_s * 48000 + S.segment_samples
            last_listen_end = (m - 1) * S.listen_slice_s * 48000 + S.listen_segment_samples
            self.assertGreaterEqual(total, last_scrub_end, D)
            self.assertGreaterEqual(total, last_listen_end, D)
            self.assertEqual(total, max(last_scrub_end, last_listen_end), D)

    def test_slice_bytes(self):
        pcm = bytes(range(256)) * 100
        self.assertEqual(slice_bytes(pcm, 0, 10), pcm[:40])
        self.assertEqual(slice_bytes(pcm, 5, 10), pcm[20:60])
        with self.assertRaises(JobError):
            slice_bytes(pcm, 6400 - 5, 10)
        self.assertEqual(BYTES_PER_FRAME, 4)

    def test_opusenc_argv(self):
        argv = opusenc_argv(48, S, Path("/x/0000.opus"))
        for flag in ("--raw", "--raw-rate", "48000", "--raw-chan", "2", "--bitrate", "48", "--vbr",
                     "--comp", "10", "--framesize", "20", "--discard-comments", "--padding"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-2:], ["-", "/x/0000.opus"])


class TestOgg(unittest.TestCase):
    def test_fixture(self):
        info = opus_info(FIX / "tone-100ms.opus")
        self.assertEqual(info.samples, 4800)       # 0.1 s @ 48 kHz, verified against opusdec
        self.assertEqual(info.pre_skip, 312)
        self.assertEqual(info.channels, 2)
        self.assertEqual(info.input_rate, 48000)

    def test_truncated(self):
        data = (FIX / "tone-100ms.opus").read_bytes()
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".opus") as f:
            f.write(data[:-100]); f.flush()
            with self.assertRaises(OggError):
                opus_info(Path(f.name))
        with tempfile.NamedTemporaryFile(suffix=".opus") as f:
            f.write(b"RIFF" + data[4:]); f.flush()
            with self.assertRaises(OggError):
                opus_info(Path(f.name))


if __name__ == "__main__":
    unittest.main()
