"""calibrate.calibrate() with the engine and ffmpeg processes faked: the argv it builds must let
ffmpeg read what each engine wrote. FluidSynth writes headerless f32le (engines/fluid.py), which
ffmpeg cannot probe — the reference decode used to run without `-f f32le -ar … -ac …` and died
before a single onset was measured."""
import array
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sfr import calibrate as c
from sfr.config import Paths

from .helpers import ENGINES_JSON, SETTINGS, adl_variant, sf2_variant, song

RAW_ARGS = ["-f", "f32le", "-ar", "48000", "-ac", "2"]


def click_pcm(clicks_s, total_s, sr=48000) -> bytes:
    """Mono s16le with a 50 ms burst (2 ms ramp) 5 samples after every click time."""
    a = array.array("h", [0] * (total_s * sr))
    for t in clicks_s:
        at = int(t * sr) + 5
        for i in range(2400):
            a[at + i] = int(min(1.0, i / 96) * 20000 * (1 if i % 2 else -1))
    return a.tobytes()


class TestMono48k(unittest.TestCase):
    def test_raw_format_args_precede_the_input(self):
        with mock.patch.object(c, "run", return_value=mock.Mock(stdout=b"\x01\x00\x02\x00\x03")) as run:
            out = c._mono_48k(Path("/t/raw.f32le"), RAW_ARGS)
        argv = run.call_args.args[0]
        i = argv.index("-i")
        self.assertEqual(argv[i - 6:i + 2], RAW_ARGS + ["-i", "/t/raw.f32le"])
        self.assertEqual(list(out), [1, 2])                 # odd trailing byte dropped
        with mock.patch.object(c, "run", return_value=mock.Mock(stdout=b"")) as run:
            c._mono_48k(Path("/t/raw.wav"))
        self.assertEqual(run.call_args.args[0][3:5], ["-i", "/t/raw.wav"])   # self-describing WAV: nothing added


class TestCalibrateRun(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.paths = Paths(fonts=root / "fonts", songs=root / "songs", catalog=root / "catalog", roms=root / "roms",
                           work=root / "work", out=root / "out", banks=Path("/opt/banks"))
        self.paths.fonts.mkdir()
        self.font = sf2_variant(1, bytes_=1024)
        (self.paths.fonts / self.font["source"]["file"]).write_bytes(b"\0" * 1024)   # fluid's pre hook checks size

    def tearDown(self):
        self.td.cleanup()

    def test_fluidsynth_raw_output_is_decoded_with_its_format(self):
        clicks = (0.0, 1.0, 3.0, 4.0)                    # short stand-in for the 170 s track
        pcm = click_pcm(clicks, int(clicks[-1]) + 4)
        calls = []

        def fake_run(argv, **kw):
            calls.append((argv, kw))
            return mock.Mock(stdout=pcm if argv[0] == "ffmpeg" else b"", stderr=b"")

        reps = {"fluidsynth": self.font, "adlmidi": adl_variant(58)}
        with mock.patch.object(c, "CLICKS", clicks), mock.patch.object(c, "run", side_effect=fake_run):
            results = c.calibrate(self.paths, SETTINGS, ENGINES_JSON, reps, echo=lambda *_: None)

        by_engine = {}
        for argv, kw in calls:
            if argv[0] == "ffmpeg":
                by_engine.setdefault(cur, {})["ffmpeg"] = argv
            else:
                cur = argv[0]
                by_engine.setdefault(cur, {})["engine"] = (argv, kw)
        # the fluidsynth decode names the raw format right before -i <raw.f32le>
        ff = by_engine["fluidsynth"]["ffmpeg"]
        i = ff.index("-i")
        self.assertEqual(ff[i - 6:i], RAW_ARGS)
        self.assertTrue(ff[i + 1].endswith("raw.f32le"))
        # ...and is capped the way render.run_job caps it (a click that never decays)
        argv, kw = by_engine["fluidsynth"]["engine"]
        self.assertEqual(kw["stop_when"], (Path(ff[i + 1]), (int(clicks[-1]) + 4 + 2) * 48000 * 8))
        self.assertEqual(kw["rlimit_as"], 8 * 1024 ** 3)
        # a WAV-writing engine gets no format override
        ad = by_engine["adlmidiplay"]["ffmpeg"]
        self.assertEqual(ad[3], "-i")
        self.assertTrue(ad[4].endswith(".wav"))
        # identical audio from both -> zero offset, zero drift, and a report on disk
        self.assertEqual(results["fluidsynth"]["start_offset_s"], 0.0)
        self.assertEqual(results["adlmidi"]["start_offset_s"], 0.0)
        self.assertEqual(results["adlmidi"]["drift_ppm"], 0.0)
        self.assertEqual(json.loads((self.paths.work / "calib" / "report.json").read_text())["adlmidi"]["onsets"],
                         results["adlmidi"]["onsets"])


if __name__ == "__main__":
    unittest.main()
