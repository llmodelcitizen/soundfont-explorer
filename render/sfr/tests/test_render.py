"""run_job with every external step faked (no engines, no ffmpeg, no opusenc): what it leaves
under work/renders/<song>/<variant>, and in particular what becomes of master.flac (#13)."""
import tempfile
import unittest
from dataclasses import replace
from unittest import mock
from pathlib import Path

from sfr import render as render_mod
from sfr.config import Paths, RenderSettings
from sfr.encode import BYTES_PER_FRAME, padded_length_samples
from sfr.engines import RenderSpec
from sfr.jobs import Job, State, classify, read_meta, write_meta
from sfr.render import run_job
from sfr.sched import Completed
from .helpers import ENGINES_JSON, adl_variant, song
from .test_pack_manifest import FIX, _fake_render


class TestRunJob(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.paths = Paths(work=root / "work", out=root / "out")
        # tiny settings so the fixture's 4800 samples pass the structural check
        self.settings = RenderSettings(slice_s=1, listen_slice_s=1, lead_in_s=0.0, lead_out_s=0.0,
                                       sample_rate=4800, pack_size=3)
        self.job = Job(song(D=3), adl_variant(), self.settings, ENGINES_JSON["engines"]["adlmidi"])

    def tearDown(self):
        self.td.cleanup()

    def _run(self, job: Job | None = None, **kw):
        """run_job with fakes in place; returns (outcome, steps) where steps records which of
        render / master / decode ran (decode carries the bytes it read from master.flac)."""
        job = job or self.job
        data = FIX.read_bytes()
        steps: list = []

        class FakeEngine:
            @staticmethod
            def spec(job, paths, engines_json, tmp):
                out = tmp / "out.wav"
                return RenderSpec(argv=["fake-engine"], cwd=tmp, out_wav=out,
                                  pre=[lambda: out.write_bytes(bytes(4096))])

        def fake_run(argv, **_):
            steps.append("render")
            return Completed(argv, 0, b"", b"", 0.1, 0.1)

        def fake_measure(wav, settings, in_args=None):
            return {"input_i": -20.0, "input_tp": -1.0, "input_lra": 5.0}

        def fake_master_pcm(raw_wav, gain, settings, duration_s, *, master_flac=None, **_):
            steps.append("master")
            if master_flac is not None:
                master_flac.write_bytes(b"fLaC-new")
            return bytes(padded_length_samples(settings, duration_s) * BYTES_PER_FRAME)

        def fake_decode_padded(master_flac, settings, duration_s):
            steps.append(("decode", master_flac.read_bytes()))
            return bytes(padded_length_samples(settings, duration_s) * BYTES_PER_FRAME)

        def fake_encode_tiers(pcm, seg_dir, listen_dir, settings, duration_s, serial_seed=""):
            n, m = settings.n_slices(duration_s), settings.n_listen(duration_s)
            for i in range(n):
                (seg_dir / f"{i:04d}.opus").write_bytes(data)
            for k in range(m):
                (listen_dir / f"{k:03d}.opus").write_bytes(data)
            return {"seg": [len(data)] * n, "listen": [len(data)] * m}

        with mock.patch.object(render_mod.engines, "get", lambda name: FakeEngine), \
                mock.patch.object(render_mod, "run", fake_run), \
                mock.patch.object(render_mod, "measure", fake_measure), \
                mock.patch.object(render_mod, "master_pcm", fake_master_pcm), \
                mock.patch.object(render_mod, "decode_padded", fake_decode_padded), \
                mock.patch.object(render_mod, "encode_tiers", fake_encode_tiers):
            out = run_job(job, self.paths, engines_json=ENGINES_JSON, **kw)
        return out, steps

    def _stale_master(self):
        """What a --keep-masters run of an earlier spec leaves behind: its FLAC and its meta."""
        j, p = self.job, self.paths
        j.master_path(p).parent.mkdir(parents=True, exist_ok=True)
        j.master_path(p).write_bytes(b"fLaC-old")
        write_meta(j.meta_path(p), {"status": "ok", "spec_hash": "old", "master_hash": "old",
                                    "master_kept": True})

    def test_render_without_keep_removes_the_previous_master(self):
        """The issue's scenario (#13): spec A kept its master, spec B re-rendered without
        --keep-masters, then an encode-only change must not re-encode spec A's audio."""
        j, p = self.job, self.paths
        self._stale_master()
        out, steps = self._run(keep_masters=False)
        self.assertEqual(out.status, "done", out.reason)
        self.assertIn("render", steps)
        self.assertFalse(j.master_path(p).exists())
        meta = read_meta(j.meta_path(p))
        self.assertEqual((meta["status"], meta["master_kept"], meta["sizes"]["master_bytes"]), ("ok", False, 0))
        bitrate_change = Job(j.song, j.variant, replace(self.settings, scrub_bitrate_kbps=64),
                             j.engine_meta)
        self.assertEqual(bitrate_change.master_hash, j.master_hash)
        self.assertEqual(classify(bitrate_change, p), State.STALE)      # re-render, never REENCODE

    def test_render_with_keep_replaces_the_previous_master(self):
        j, p = self.job, self.paths
        self._stale_master()
        out, steps = self._run(keep_masters=True)
        self.assertEqual(out.status, "done", out.reason)
        self.assertEqual(j.master_path(p).read_bytes(), b"fLaC-new")
        meta = read_meta(j.meta_path(p))
        self.assertEqual((meta["master_kept"], meta["sizes"]["master_bytes"]), (True, len(b"fLaC-new")))

    def test_reencode_vouches_for_the_master_it_decoded(self):
        """A re-encode reads master.flac and leaves it in place, so its meta must say
        master_kept=true whatever --keep-masters was: otherwise classify() (#13) refuses the
        next re-encode and the manifest rejects the render as inconsistent."""
        j, p = self.job, self.paths
        _fake_render(j, p, spec="old")                  # same master_hash, old encode params
        self.assertEqual(classify(j, p), State.REENCODE)
        out, steps = self._run(keep_masters=False)
        self.assertEqual(out.status, "done", out.reason)
        self.assertEqual(steps, [("decode", b"fLaC")])
        self.assertTrue(j.master_path(p).exists())
        meta = read_meta(j.meta_path(p))
        self.assertEqual((meta["status"], meta["master_kept"], meta["reencoded_from"]), ("ok", True, j.render_hash))
        again = Job(j.song, j.variant, replace(self.settings, scrub_bitrate_kbps=64),
                    j.engine_meta)
        self.assertEqual(classify(again, p), State.REENCODE)


if __name__ == "__main__":
    unittest.main()
