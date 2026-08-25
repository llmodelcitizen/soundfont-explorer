import unittest
from pathlib import Path

from sfr.config import RenderSettings
from sfr.jobs import Job, State, classify, order_jobs, plan_jobs, variant_allowed_for_song, write_meta
from sfr.config import Paths
from .helpers import ENGINES_JSON, SETTINGS, adl_variant, sf2_variant, song
import tempfile


class TestSettings(unittest.TestCase):
    def test_numbers_match_plan(self):
        s = RenderSettings()
        self.assertEqual(s.segment_samples, 102720)
        self.assertEqual(s.listen_segment_samples, 486720)
        self.assertEqual(s.lead_in_samples, 5760)
        self.assertEqual(s.lead_out_samples, 960)
        self.assertEqual(s.n_slices(188), 94)
        self.assertEqual(s.n_listen(188), 19)
        self.assertEqual(s.n_listen(190), 19)
        self.assertEqual(s.n_listen(192), 20)

    def test_from_json_checks_derived(self):
        RenderSettings.from_json({"segment_samples": 102720, "listen_segment_samples": 486720})
        with self.assertRaises(AssertionError):
            RenderSettings.from_json({"segment_samples": 1})


class TestHashes(unittest.TestCase):
    def job(self, **kw):
        return Job(song(), sf2_variant(), SETTINGS, ENGINES_JSON["engines"]["fluidsynth"], **kw)

    def test_stable_and_distinct(self):
        a, b = self.job(), self.job()
        self.assertEqual(a.spec_hash, b.spec_hash)
        self.assertEqual(len(a.master_hash), 12)
        self.assertEqual(len(a.render_hash), 12)
        self.assertEqual(len(a.spec_hash), 16)
        other = Job(song(), sf2_variant(2), SETTINGS, ENGINES_JSON["engines"]["fluidsynth"])
        self.assertNotEqual(a.master_hash, other.master_hash)

    def test_encode_change_keeps_master_hash(self):
        a = self.job()
        s2 = RenderSettings(scrub_bitrate_kbps=64)
        b = Job(song(), sf2_variant(), s2, ENGINES_JSON["engines"]["fluidsynth"])
        self.assertEqual(a.master_hash, b.master_hash)
        self.assertNotEqual(a.render_hash, b.render_hash)
        self.assertNotEqual(a.spec_hash, b.spec_hash)

    def test_engine_version_changes_master(self):
        a = self.job()
        e = dict(ENGINES_JSON["engines"]["fluidsynth"], version="2.5.0")
        b = Job(song(), sf2_variant(), SETTINGS, e)
        self.assertNotEqual(a.master_hash, b.master_hash)

    def test_lufs_target_changes_master(self):
        a = self.job()
        b = Job(song(), sf2_variant(), RenderSettings(lufs_target=-14), ENGINES_JSON["engines"]["fluidsynth"])
        self.assertNotEqual(a.master_hash, b.master_hash)


class TestPlanning(unittest.TestCase):
    def test_exclusion_policy(self):
        s = song(classes=["full_gm", "melodic_only", "partial", "single_instrument:piano"])
        self.assertTrue(variant_allowed_for_song(sf2_variant(1, completeness="full_gm"), s))
        self.assertTrue(variant_allowed_for_song(sf2_variant(2, completeness="single_instrument", instrument="piano"), s))
        self.assertFalse(variant_allowed_for_song(sf2_variant(3, completeness="single_instrument", instrument="guitar"), s))
        self.assertFalse(variant_allowed_for_song(sf2_variant(4, completeness="drums_only"), s))
        self.assertTrue(variant_allowed_for_song(adl_variant(), s))

    def test_plan_filters_and_orders(self):
        variants = [sf2_variant(1, bytes_=10 << 20), sf2_variant(2, bytes_=1200 << 20), adl_variant(58),
                    adl_variant(0), dict(sf2_variant(3), publish=False),
                    {"id": "opn-x", "engine": "opnmidi", "facets": {}, "source": {}, "publish": True},
                    dict(sf2_variant(4), requires_rom=True, romset="sc55mk2")]
        jobs = plan_jobs([song(), song("joplin", 144)], variants, SETTINGS, ENGINES_JSON, available_roms=set())
        keys = [j.key for j in jobs]
        # biggest font first, font-major, FM last; unpublished, missing-engine and missing-ROM excluded
        self.assertEqual(keys[:2], [f"freedoom-e1m1/{sf2_variant(2)['id']}", f"joplin/{sf2_variant(2)['id']}"])
        self.assertNotIn("freedoom-e1m1/" + sf2_variant(3)["id"], keys)
        self.assertTrue(all("opn-x" not in k for k in keys))
        self.assertEqual(len(jobs), 2 * 4)
        self.assertEqual([j.variant_id for j in jobs[-4:]], ["adl-b0", "adl-b0", "adl-b58", "adl-b58"])
        # --engine filter
        fm = plan_jobs([song()], variants, SETTINGS, ENGINES_JSON, engines={"adlmidi"})
        self.assertEqual({j.engine for j in fm}, {"adlmidi"})

    def test_order_is_deterministic(self):
        variants = [sf2_variant(i, bytes_=(i % 3) << 20) for i in range(9)]
        a = plan_jobs([song()], variants, SETTINGS, ENGINES_JSON)
        b = plan_jobs([song()], list(reversed(variants)), SETTINGS, ENGINES_JSON)
        self.assertEqual([j.key for j in a], [j.key for j in b])


class TestClassify(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.paths = Paths(work=Path(self.td.name) / "work", out=Path(self.td.name) / "out")
        self.job = Job(song(D=4), adl_variant(), SETTINGS, ENGINES_JSON["engines"]["adlmidi"])

    def tearDown(self):
        self.td.cleanup()

    def _outputs(self):
        j, p = self.job, self.paths
        j.master_path(p).parent.mkdir(parents=True, exist_ok=True)
        j.master_path(p).write_bytes(b"x")
        for i in range(j.n_slices):
            (j.seg_dir(p)).mkdir(exist_ok=True)
            (j.seg_dir(p) / f"{i:04d}.opus").write_bytes(b"x")
        for k in range(j.n_listen):
            (j.listen_dir(p)).mkdir(exist_ok=True)
            (j.listen_dir(p) / f"{k:03d}.opus").write_bytes(b"x")

    def test_states(self):
        j, p = self.job, self.paths
        self.assertEqual(classify(j, p), State.TODO)
        write_meta(j.meta_path(p), {"status": "failed", "spec_hash": j.spec_hash})
        self.assertEqual(classify(j, p), State.FAILED)
        write_meta(j.meta_path(p), {"status": "ok", "spec_hash": j.spec_hash, "master_hash": j.master_hash})
        self.assertEqual(classify(j, p), State.STALE)   # outputs missing, no master
        self._outputs()
        self.assertEqual(classify(j, p), State.DONE)
        write_meta(j.meta_path(p), {"status": "ok", "spec_hash": "old", "master_hash": j.master_hash})
        self.assertEqual(classify(j, p), State.REENCODE)
        write_meta(j.meta_path(p), {"status": "ok", "spec_hash": "old", "master_hash": "old"})
        self.assertEqual(classify(j, p), State.STALE)


def rom_variant(romset="sc55-mk2"):
    return dict(sf2_variant(9), id=romset, requires_rom=True, romset=romset)


class TestRomIdentity(unittest.TestCase):
    """The ROM images are a render input: roms/<romset>/ contents key requires_rom masters."""
    EMETA = ENGINES_JSON["engines"]["fluidsynth"]

    def test_non_rom_hashes_are_pinned(self):
        # every published object is keyed by master_hash, so its inputs are a contract: adding the
        # ROM digest (#21) must not move a single non-ROM hash. Values computed before the change.
        self.assertEqual(Job(song(), sf2_variant(), SETTINGS, self.EMETA).master_hash, "e4c1e4cfb94c")
        self.assertEqual(Job(song(), adl_variant(), SETTINGS, ENGINES_JSON["engines"]["adlmidi"]).master_hash,
                         "5e4f30ba360f")
        self.assertEqual(Job(song(), sf2_variant(), SETTINGS, self.EMETA, rom_sha256="f" * 64).master_hash,
                         "e4c1e4cfb94c")   # ignored unless requires_rom

    def test_rom_contents_key_rom_variants(self):
        a = Job(song(), rom_variant(), SETTINGS, self.EMETA, rom_sha256="a" * 64)
        b = Job(song(), rom_variant(), SETTINGS, self.EMETA, rom_sha256="b" * 64)
        none = Job(song(), rom_variant(), SETTINGS, self.EMETA)
        self.assertNotEqual(a.master_hash, b.master_hash)
        self.assertNotEqual(a.master_hash, none.master_hash)
        self.assertEqual(a.master_hash, Job(song(), rom_variant(), SETTINGS, self.EMETA, rom_sha256="a" * 64).master_hash)

    def test_rom_digest_is_content_only(self):
        from sfr.jobs import rom_digest
        with tempfile.TemporaryDirectory() as td:
            d1, d2, d3 = (Path(td) / n for n in ("one", "two", "three"))
            for d in (d1, d2, d3):
                d.mkdir()
            (d1 / "MT32_CONTROL.ROM").write_bytes(b"ctrl-1.07")
            (d1 / "MT32_PCM.ROM").write_bytes(b"pcm")
            (d2 / "b.bin").write_bytes(b"ctrl-1.07")          # same contents, other names/order
            (d2 / "a.bin").write_bytes(b"pcm")
            (d2 / "notes").mkdir()                              # sub-directories are not ROMs
            (d2 / "notes" / "x").write_bytes(b"whatever")
            (d3 / "MT32_CONTROL.ROM").write_bytes(b"ctrl-2.04")   # another control ROM version
            (d3 / "MT32_PCM.ROM").write_bytes(b"pcm")
            self.assertEqual(rom_digest(d1), rom_digest(d2))
            self.assertNotEqual(rom_digest(d1), rom_digest(d3))
            self.assertEqual(len(rom_digest(d1)), 64)

    def test_plan_jobs_threads_the_digest(self):
        variants = [rom_variant(), sf2_variant(1)]
        jobs = plan_jobs([song()], variants, SETTINGS, ENGINES_JSON, available_roms={"sc55-mk2": "d" * 64})
        by_id = {j.variant_id: j for j in jobs}
        self.assertEqual(by_id["sc55-mk2"].rom_sha256, "d" * 64)
        self.assertIsNone(by_id[sf2_variant(1)["id"]].rom_sha256)
        # a plain set still filters, None still means "do not filter"
        self.assertEqual([j.variant_id for j in plan_jobs([song()], variants, SETTINGS, ENGINES_JSON, available_roms=set())],
                         [sf2_variant(1)["id"]])
        self.assertEqual({j.variant_id for j in plan_jobs([song()], variants, SETTINGS, ENGINES_JSON)},
                         {"sc55-mk2", sf2_variant(1)["id"]})

    def test_cli_available_roms_digests_each_directory(self):
        from sfr.cli import available_roms
        from sfr.jobs import rom_digest
        with tempfile.TemporaryDirectory() as td:
            roms = Path(td) / "roms"
            self.assertEqual(available_roms(Paths(roms=roms)), {})
            (roms / "mt32").mkdir(parents=True)
            (roms / "mt32" / "MT32_PCM.ROM").write_bytes(b"pcm")
            (roms / "sc55-mk2").mkdir()
            (roms / "README").write_text("not a romset")
            got = available_roms(Paths(roms=roms))
            self.assertEqual(set(got), {"mt32", "sc55-mk2"})
            self.assertEqual(got["mt32"], rom_digest(roms / "mt32"))


if __name__ == "__main__":
    unittest.main()
