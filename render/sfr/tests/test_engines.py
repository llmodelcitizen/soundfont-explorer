"""Engine adapters build a RenderSpec from fake variants (no docker, no engines): argv shape, cwd,
out_wav naming, weights, native rates and the pre hooks that the M2b engine probes made load-bearing."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sfr import engines
from sfr.config import Paths
from sfr.jobs import Job
from sfr.sched import JobError

from .helpers import ENGINES_JSON, SETTINGS, adl_variant, sf2_variant, song

ENGINES = {
    "render": {},
    "engines": {
        **ENGINES_JSON["engines"],
        "opnmidi": {"label": "libOPNMIDI", "version": "1.6.1", "commit": "3e66123", "native_rate": 44100,
                    "base_args": ["-f32", "-vm", "0", "--gain", "2.0"], "chips": 2, "chips_flag": "--chips",
                    "cores": {"nuked-3438": "--emu-nuked-3438", "mame-opna": "--emu-mame-opna"},
                    "start_offset_s": None, "drift_ppm": None},
        "edmidi": {"label": "libEDMIDI", "version": "1.0.0", "commit": "be48aeb", "native_rate": 48000,
                   "base_args": ["-r", "48000", "-n", "8", "-f", "f32"]},
        "timidity": {"label": "TiMidity++", "version": "2.14.0", "native_rate": 48000,
                     "base_args": ["-c", "/etc/timidity/freepats.cfg", "-Ow2", "-s", "48000", "-EFreverb=d", "-EFchorus=d",
                                   "-A", "25", "--preserve-silence"]},
        "sc55": {"label": "Nuked-SC55", "version": "0.7.0", "commit": "a48ef92", "requires_rom": True,
                 "native_rate": {"mk2": 66207, "mk1": 64000, "jv880": 64000, "sc155": 64000},
                 "base_args": ["-f", "f32", "--end", "release", "-n", "1"],
                 "romset_flag": {"sc55-mk2": "mk2", "sc55-mk1": "mk1", "sc55-jv880": "jv880"}},
        "munt": {"label": "Munt", "version": "2.8.3", "commit": "6e7c01f", "requires_rom": True, "native_rate": 32000,
                 "base_args": ["--quiet", "-f", "--src-quality", "3", "--renderer-type", "1", "--output-sample-format", "1",
                               "--record-max-start-silence", "-1", "--record-max-end-silence", "-1"]},
    },
}


def opn_variant(slug="xg", core="nuked-3438", suffix=""):
    return {"id": f"opn-{slug}{suffix}", "engine": "opnmidi", "chip_family": "opn2", "type": "fm", "publish": True,
            "facets": {"completeness": "full_gm"}, "bank": {"kind": "file", "dir": "wopn", "file": f"{slug}.wopn", "sha256": "b" * 64},
            "source": {"file": None, "sha256": "b" * 64, "bytes": 186204}, "render": {"core": core}}


def edm_variant(module="all"):
    return {"id": f"edm-{module}", "engine": "edmidi", "chip_family": "opll", "type": "fm", "publish": True, "module": module,
            "facets": {"completeness": "full_gm"}, "source": {}, "render": {"core": "emu2413+emu2212", "module": module}}


def gus_variant():
    return {"id": "gus-freepats", "engine": "timidity", "chip_family": "gus", "type": "sampled", "publish": True,
            "facets": {"completeness": "partial"}, "source": {}, "render": {"core": "timidity"}}


def sc55_variant(family="mk2"):
    return {"id": f"sc55-{family}", "engine": "sc55", "chip_family": "pcm_rom", "type": "sampled", "publish": True,
            "requires_rom": True, "romset": f"sc55-{family}", "rom": {"family": family, "dir": f"sc55-{family}"},
            "facets": {"completeness": "full_gm"}, "source": {}, "render": {"core": "nuked-sc55"}}


def munt_variant(model="mt32"):
    return {"id": model, "engine": "munt", "chip_family": "la", "type": "la", "publish": True, "requires_rom": True,
            "romset": model, "model": model, "rom": {"machine": model, "dir": model},
            "facets": {"completeness": "full_gm"}, "source": {}, "render": {"core": "mt32emu", "model": model}}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.paths = Paths(fonts=root / "fonts", songs=root / "songs", catalog=root / "catalog", roms=root / "roms",
                           work=root / "work", out=root / "out", banks=Path("/opt/banks"))
        self.tmp = root / "work" / "tmp" / "job"
        self.tmp.mkdir(parents=True)
        (root / "songs").mkdir()
        (root / "songs" / "freedoom-e1m1.mid").write_bytes(b"MThd")
        self.song = song(root=root / "songs")

    def tearDown(self):
        self.td.cleanup()

    def job(self, variant):
        return Job(self.song, variant, SETTINGS, ENGINES["engines"][variant["engine"]])

    def spec(self, variant):
        return engines.get(variant["engine"]).spec(self.job(variant), self.paths, ENGINES, self.tmp)


class TestDispatch(Base):
    def test_get_every_engine(self):
        for eid in ("adlmidi", "fluidsynth", "opnmidi", "edmidi", "timidity", "sc55", "munt"):
            self.assertTrue(hasattr(engines.get(eid), "spec"), eid)
        with self.assertRaises(KeyError):
            engines.get("nope")


class TestAdlAndFluid(Base):
    def test_adl(self):
        s = self.spec(adl_variant(58))
        self.assertEqual(s.argv, ["adlmidiplay", "freedoom-e1m1.mid", "-f32", "-vm", "0", "--gain", "2.0", "--emu-nuked", "58", "1"])
        self.assertEqual(s.cwd, self.tmp)
        self.assertEqual(s.out_wav, self.tmp / "freedoom-e1m1.mid.wav")
        self.assertEqual((s.weight, s.native_rate), (1, 44100))
        for pre in s.pre:
            pre()
        self.assertTrue((self.tmp / "freedoom-e1m1.mid").is_symlink())

    def test_fluid_weight(self):
        v = sf2_variant(1, bytes_=1200 << 20)
        s = self.spec(v)
        self.assertEqual(s.argv[:3], ["fluidsynth", "-F", str(self.tmp / "raw.f32le")])
        self.assertIn("raw", s.argv)          # -T raw: headerless so a kill mid-write is harmless
        self.assertEqual(s.raw_format, ("f32le", 48000, 2))
        self.assertEqual(s.max_out_bytes, (self.job(v).duration_s + 2) * 48000 * 8)
        self.assertEqual(s.weight, 5)
        self.assertEqual(s.timeout_s, 1800)
        self.assertEqual(s.native_rate, 48000)
        self.assertIn("synth.dynamic-sample-loading=1", s.argv)


class TestOpn(Base):
    def test_argv_order_options_bank_midi(self):
        s = self.spec(opn_variant("xg"))
        self.assertEqual(s.argv, ["opnmidiplay", "-f32", "-vm", "0", "--gain", "2.0", "--chips", "2", "--emu-nuked-3438",
                                  "/opt/banks/wopn/xg.wopn", "freedoom-e1m1.mid"])
        self.assertEqual(s.cwd, self.tmp)
        self.assertEqual(s.out_wav, self.tmp / "freedoom-e1m1.mid.wav")   # <input>.wav, WAVE_ONLY build
        self.assertEqual((s.weight, s.timeout_s, s.native_rate), (1, 900, 44100))
        self.assertEqual(len(s.pre), 1)
        s.pre[0]()
        link = self.tmp / "freedoom-e1m1.mid"
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(self.song["_dir"] / "freedoom-e1m1.mid"))
        s.pre[0]()   # idempotent (re-run after a failed attempt)
        self.assertTrue(link.is_symlink())

    def test_opna_pass(self):
        s = self.spec(opn_variant("gs-dmxopn2", core="mame-opna", suffix="-opna"))
        self.assertIn("--emu-mame-opna", s.argv)
        self.assertEqual(s.argv[-2], "/opt/banks/wopn/gs-dmxopn2.wopn")
        self.assertLess(s.argv.index("--emu-mame-opna"), s.argv.index("/opt/banks/wopn/gs-dmxopn2.wopn"))

    def test_unknown_core_or_bank(self):
        with self.assertRaises(ValueError):
            self.spec(opn_variant("xg", core="nuked"))        # not an opnmidiplay flag -> would be taken as the bank path
        bad = opn_variant("xg")
        bad["bank"] = {"kind": "embedded", "number": 0}
        with self.assertRaises(ValueError):
            self.spec(bad)
        bad["bank"] = {"kind": "file", "file": "../etc/passwd"}
        with self.assertRaises(ValueError):
            self.spec(bad)

    def test_bank_dir_from_paths(self):
        self.paths.banks = Path("/somewhere/banks")
        s = self.spec(opn_variant("fmmidi"))
        self.assertEqual(s.argv[-2], "/somewhere/banks/wopn/fmmidi.wopn")


class TestEdm(Base):
    def test_modules(self):
        for m in ("opll", "scc", "all"):
            s = self.spec(edm_variant(m))
            self.assertEqual(s.argv, ["edmidi-render", "-r", "48000", "-n", "8", "-f", "f32", "-m", m,
                                      "-o", str(self.tmp / "raw.wav"), str(self.song["_dir"] / "freedoom-e1m1.mid")])
            self.assertEqual(s.out_wav, self.tmp / "raw.wav")
            self.assertEqual((s.weight, s.native_rate), (1, 48000))
            self.assertEqual(s.pre, [])

    def test_psg_rejected(self):
        with self.assertRaises(ValueError):
            self.spec(edm_variant("psg"))


class TestTimidity(Base):
    def test_argv(self):
        s = self.spec(gus_variant())
        self.assertEqual(s.argv[0], "timidity")
        self.assertEqual(s.argv[-3:], ["-o", str(self.tmp / "raw.wav"), str(self.song["_dir"] / "freedoom-e1m1.mid")])
        self.assertIn("--preserve-silence", s.argv)
        self.assertEqual(s.argv[s.argv.index("-c") + 1], "/etc/timidity/freepats.cfg")
        self.assertEqual((s.weight, s.native_rate, s.out_wav), (1, 48000, self.tmp / "raw.wav"))

    def test_required_flags_enforced(self):
        eng = {"render": {}, "engines": dict(ENGINES["engines"])}
        eng["engines"]["timidity"] = dict(ENGINES["engines"]["timidity"], base_args=["-Ow2", "-s", "48000"])
        with self.assertRaises(ValueError):
            engines.get("timidity").spec(self.job(gus_variant()), self.paths, eng, self.tmp)


class TestSc55(Base):
    def test_argv_and_rate(self):
        s = self.spec(sc55_variant("mk2"))
        self.assertEqual(s.argv, ["nuked-sc55-render", "-f", "f32", "--end", "release", "-n", "1",
                                  "-d", str(self.paths.roms / "sc55-mk2"), "--romset", "mk2",
                                  "-o", str(self.tmp / "raw.wav"), str(self.song["_dir"] / "freedoom-e1m1.mid")])
        self.assertEqual(s.native_rate, 66207)
        self.assertEqual(self.spec(sc55_variant("mk1")).native_rate, 64000)
        self.assertEqual(self.spec(sc55_variant("jv880")).native_rate, 64000)

    def test_family_from_engines_json_or_id(self):
        v = sc55_variant("mk1")
        del v["rom"]
        self.assertIn("mk1", self.spec(v).argv)                       # via romset_flag table
        v = sc55_variant("st")
        del v["rom"]
        self.assertEqual(self.spec(v).argv[self.spec(v).argv.index("--romset") + 1], "st")   # via id prefix
        with self.assertRaises(ValueError):
            self.spec(sc55_variant("sc99"))

    def test_missing_rom_dir_is_a_job_error(self):
        s = self.spec(sc55_variant("mk2"))
        with self.assertRaises(JobError) as cm:
            s.pre[0]()
        self.assertEqual(cm.exception.reason, "missing-rom")
        (self.paths.roms / "sc55-mk2").mkdir(parents=True)
        s.pre[0]()   # present -> ok (the engine itself checks the ROM hashes)


class TestMunt(Base):
    def test_argv(self):
        for model in ("mt32", "cm32l"):
            s = self.spec(munt_variant(model))
            self.assertEqual(s.argv[0], "mt32emu-smf2wav")
            self.assertEqual(s.argv[-7:], ["-m", str(self.paths.roms / model), "-i", model, "-o", str(self.tmp / "raw.wav"),
                                           str(self.song["_dir"] / "freedoom-e1m1.mid")])
            self.assertIn("--record-max-start-silence", s.argv)
            self.assertIn("--src-quality", s.argv)
            self.assertNotIn("-q", s.argv)
            self.assertEqual((s.weight, s.native_rate), (1, 32000))

    def test_model_validation_and_rom_dir(self):
        with self.assertRaises(ValueError):
            self.spec(munt_variant("sc55"))
        s = self.spec(munt_variant("cm32l"))
        with self.assertRaises(JobError):
            s.pre[0]()
        (self.paths.roms / "cm32l").mkdir(parents=True)
        s.pre[0]()


class TestHooks(Base):
    def test_weight_units(self):
        self.assertEqual(engines.weight_units(self.job(sf2_variant(1, bytes_=1200 << 20))), 5)
        self.assertEqual(engines.weight_units(self.job(sf2_variant(2, bytes_=1))), 1)
        for v in (adl_variant(58), opn_variant("xg"), edm_variant("all"), gus_variant(), sc55_variant(), munt_variant()):
            self.assertEqual(engines.weight_units(self.job(v)), 1, v["id"])

    def test_adl_preflight_checks_flags_against_usage(self):
        from sfr.engines import adl
        jobs = [self.job(adl_variant(58)), self.job(adl_variant(1, core="esfmu", suffix="-esfmu")), self.job(adl_variant(0))]
        usage = mock.Mock(stdout="Usage: adlmidiplay ... --emu-nuked --emu-dosbox ...")
        with mock.patch.object(adl.subprocess, "run", return_value=usage) as run:
            self.assertEqual(adl.preflight(jobs, ENGINES),
                             ["adlmidiplay does not know --emu-esfmu (an unknown flag is silently treated as a bank file "
                              "— MVP footgun)"])
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["adlmidiplay", "--help"])
            usage.stdout += " --emu-esfmu"
            self.assertEqual(adl.preflight(jobs, ENGINES), [])


class TestIdentity(Base):
    def test_bank_sha_and_romset_in_master_hash(self):
        a = self.job(opn_variant("xg")).master_hash
        v = opn_variant("xg")
        v["bank"]["sha256"] = "c" * 64
        self.assertNotEqual(a, self.job(v).master_hash)
        self.assertNotEqual(self.job(sc55_variant("mk2")).master_hash, self.job(sc55_variant("mk1")).master_hash)
        self.assertNotEqual(self.job(edm_variant("opll")).master_hash, self.job(edm_variant("scc")).master_hash)

    def test_job_core(self):
        self.assertEqual(self.job(opn_variant("xg", core="mame-opna")).core, "mame-opna")
        self.assertEqual(self.job(edm_variant("scc")).core, "emu2413+emu2212")
        self.assertEqual(self.job(gus_variant()).core, "timidity")


class TestCalibrateAnalysis(unittest.TestCase):
    """calibrate.analyse / onset over synthetic onsets (the render step needs engines; this does not)."""

    def test_offset_and_drift_from_steady_state_clicks(self):
        from sfr import calibrate as c
        self.assertEqual(c.CLICKS, (0.0, 1.0, 169.0, 170.0))
        ref = [0.0018, 1.0015, 169.0015, 170.0015]
        ons = {"fluidsynth": ref,
               "adlmidi": [0.0312, 1.0005, 169.0002, 170.0002],      # 31 ms startup transient, then on time
               "slow": [0.0, 1.0100, 169.0100, 170.0269],            # 16.9 ms late at the end -> 100 ppm
               "missing": [0.0, None, 169.0, 170.0]}
        r = c.analyse(ons)
        self.assertEqual(r["fluidsynth"]["start_offset_s"], 0.0)
        self.assertEqual(r["fluidsynth"]["drift_ppm"], 0.0)
        self.assertAlmostEqual(r["adlmidi"]["start_offset_s"], -0.0010, places=4)
        self.assertAlmostEqual(r["adlmidi"]["first_click_extra_ms"], 30.7, places=1)
        self.assertLess(abs(r["adlmidi"]["end_drift_ms"]), 2.0)
        self.assertAlmostEqual(r["slow"]["drift_ppm"], 100.0, delta=0.5)
        self.assertEqual(r["missing"]["error"], "click not detected")
        with self.assertRaises(SystemExit):
            c.analyse({"adlmidi": ref})

    def test_apply_writes_only_sane_values(self):
        import json
        from sfr import calibrate as c
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "engines.json"
            p.write_text(json.dumps({"engines": {"fluidsynth": {"start_offset_s": 0.0, "drift_ppm": 0},
                                                  "adlmidi": {"start_offset_s": None, "drift_ppm": None},
                                                  "wild": {"start_offset_s": None, "drift_ppm": None},
                                                  "drifty": {"start_offset_s": None, "drift_ppm": None}}}))
            written = c.apply_to_engines_json(p, {
                "fluidsynth": {"start_offset_s": 0.0, "drift_ppm": 0.0, "end_drift_ms": 0.0},
                "adlmidi": {"start_offset_s": -0.00094, "drift_ppm": -2.2, "end_drift_ms": -0.375},
                "wild": {"start_offset_s": 0.9, "drift_ppm": 0.0, "end_drift_ms": 0.0},
                "drifty": {"start_offset_s": 0.01, "drift_ppm": 100.0, "end_drift_ms": 16.9},
                "broken": {"error": "click not detected"}})
            self.assertEqual(written, ["fluidsynth", "adlmidi", "drifty"])
            d = json.loads(p.read_text())["engines"]
            self.assertEqual((d["adlmidi"]["start_offset_s"], d["adlmidi"]["drift_ppm"]), (-0.00094, 0))   # < 2 ms end drift -> 0
            self.assertEqual((d["drifty"]["start_offset_s"], d["drifty"]["drift_ppm"]), (0.01, 100.0))
            self.assertIsNone(d["wild"]["start_offset_s"])                                              # |offset| > 0.5 s ignored

    def test_onset_detection(self):
        import array
        from sfr import calibrate as c
        sr = 48000
        a = array.array("h", [0] * (3 * sr))
        at = int(1.0 * sr) + 37
        for i in range(2400):                  # 50 ms burst with a 2 ms ramp
            a[at + i] = int(min(1.0, i / 96) * 20000 * (1 if i % 2 else -1))
        a[int(0.5 * sr)] = 150                 # sub-threshold glitch far from the click
        self.assertIsNone(c.onset(a, 2.0))     # silence there
        t = c.onset(a, 1.0)
        self.assertIsNotNone(t)
        self.assertAlmostEqual(t, at / sr, delta=0.0003)   # 2 % of peak is reached within the first 0.3 ms of the ramp


if __name__ == "__main__":
    unittest.main()
