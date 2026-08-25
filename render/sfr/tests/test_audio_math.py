import unittest
from pathlib import Path

from sfr.config import RenderSettings
from sfr.encode import BYTES_PER_FRAME, opusenc_argv, padded_length_samples, slice_bytes
from sfr.loudness import gain_db, is_silent, master_filter, parse_ebur128, parse_loudnorm
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


EBUR128_SUMMARY = """[Parsed_ebur128_0 @ 0x55] Summary:

  Integrated loudness:
    I:         -21.3 LUFS
    Threshold: -31.6 LUFS

  Loudness range:
    LRA:         5.5 LU
    Threshold: -41.5 LUFS
    LRA low:   -24.9 LUFS
    LRA high:  -19.4 LUFS

  True peak:
    Peak:       -6.5 dBFS

[out#0/null @ 0x66] video:0KiB audio:4500KiB
"""


class TestParseEbur128(unittest.TestCase):
    def test_summary(self):
        d = parse_ebur128(EBUR128_SUMMARY)
        self.assertAlmostEqual(d["input_i"], -21.3)
        self.assertAlmostEqual(d["input_tp"], -6.5)
        self.assertAlmostEqual(d["input_lra"], 5.5)

    def test_silence_floors_at_minus_70_and_inf_peak(self):
        """ffmpeg floors integrated loudness at -70 LUFS and prints `-inf` for the peak; both must
        still classify as silent (threshold -50), the way loudnorm's -inf did."""
        txt = EBUR128_SUMMARY.replace("-21.3 LUFS", "-70.0 LUFS").replace("-6.5 dBFS", "-inf dBFS")
        d = parse_ebur128(txt)
        self.assertEqual(d["input_i"], -70.0)
        self.assertEqual(d["input_tp"], float("-inf"))
        self.assertTrue(is_silent(d["input_i"], RenderSettings()))

    def test_reads_the_last_summary(self):
        d = parse_ebur128(EBUR128_SUMMARY.replace("-21.3", "-9.9") + EBUR128_SUMMARY)
        self.assertAlmostEqual(d["input_i"], -21.3)

    def test_no_summary_raises(self):
        with self.assertRaises(JobError):
            parse_ebur128("ffmpeg said nothing useful")

    def test_missing_line_is_a_parse_error_not_minus_inf(self):
        """A summary without a `Peak:` line used to yield input_tp = -inf, and gain_db would then
        ignore the true-peak ceiling entirely (min(x, +inf)); a missing `I:` line failed the job as
        "silent". Both are format changes and must stop the job with the reason in the detail."""
        no_peak = EBUR128_SUMMARY.replace("    Peak:       -6.5 dBFS\n", "")
        with self.assertRaises(JobError) as cm:
            parse_ebur128(no_peak)
        self.assertIn("Peak", cm.exception.detail)
        with self.assertRaises(JobError) as cm:
            parse_ebur128(EBUR128_SUMMARY.replace("    I:         -21.3 LUFS\n", ""))
        self.assertIn("I:", cm.exception.detail)
        # while a printed -inf is still a value (silence), see test_silence_floors_at_minus_70_and_inf_peak
        self.assertEqual(parse_ebur128(EBUR128_SUMMARY.replace("-6.5 dBFS", "-inf dBFS"))["input_tp"], float("-inf"))


class TestDeterministicSerial(unittest.TestCase):
    def test_serial_is_stable_and_per_stream(self):
        from sfr.encode import stream_serial
        self.assertEqual(stream_serial("abc", "seg/0000.opus"), stream_serial("abc", "seg/0000.opus"))
        self.assertNotEqual(stream_serial("abc", "seg/0000.opus"), stream_serial("abc", "seg/0001.opus"))
        self.assertNotEqual(stream_serial("abc", "seg/0000.opus"), stream_serial("abd", "seg/0000.opus"))
        self.assertLess(stream_serial("abc", "seg/0000.opus"), 2 ** 32)

    def test_argv_carries_serial_only_when_given(self):
        self.assertNotIn("--serial", opusenc_argv(48, S, Path("x.opus")))
        self.assertEqual(opusenc_argv(48, S, Path("x.opus"), 123)[-4:],
                         ["--serial", "123", "-", "x.opus"])
