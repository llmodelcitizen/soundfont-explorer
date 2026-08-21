import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from sfr.config import Paths
from sfr.jobs import Job, write_meta
from sfr.manifest import build_catalog, build_manifests, build_set
from sfr.pack import group_hash, groups_of, header_size, pack_bytes, unpack_bytes
from sfr.sched import WeightedSemaphore
from sfr.validate import validate_job
from .helpers import ENGINES_JSON, SETTINGS, adl_variant, sf2_variant, song

FIX = Path(__file__).parent / "fixtures" / "tone-100ms.opus"


class TestSFPK(unittest.TestCase):
    def test_roundtrip(self):
        members = [b"a" * 10, b"", b"xyz" * 1000, bytes(range(256))]
        blob = pack_bytes(members)
        self.assertEqual(blob[:4], b"SFPK")
        self.assertEqual(blob[4], 1)
        self.assertEqual(blob[5], 4)
        self.assertEqual(unpack_bytes(blob), members)
        self.assertEqual(header_size(4), 24)
        self.assertEqual(len(blob), header_size(4) + sum(map(len, members)))
        with self.assertRaises(ValueError):
            unpack_bytes(blob + b"!")
        with self.assertRaises(ValueError):
            unpack_bytes(b"NOPE" + blob[4:])

    def test_groups_and_hash(self):
        self.assertEqual([len(g) for g in groups_of(list(range(50)), 24)], [24, 24, 2])
        self.assertEqual(len(group_hash(["a", "b"])), 12)
        self.assertNotEqual(group_hash(["a", "b"]), group_hash(["b", "a"]))


class TestSemaphore(unittest.TestCase):
    def test_weighted(self):
        sem = WeightedSemaphore(4)
        self.assertEqual(sem.acquire(3), 3)
        got = []

        def worker():
            got.append(sem.acquire(2))
        t = threading.Thread(target=worker); t.start()
        time.sleep(0.05)
        self.assertEqual(got, [])          # blocked: only 1 unit free
        sem.release(3)
        t.join(1)
        self.assertEqual(got, [2])
        self.assertEqual(sem.available, 2)

    def test_weight_capped_to_total(self):
        sem = WeightedSemaphore(4)
        self.assertEqual(sem.acquire(99), 4)
        sem.release(4)
        self.assertEqual(sem.available, 4)


def _fake_render(job: Job, paths: Paths, status="ok", lufs=-21.3, gain=5.3, spec=None):
    """Lay down a plausible work/renders/<song>/<variant> using the fixture as every segment."""
    data = FIX.read_bytes()
    job.master_path(paths).parent.mkdir(parents=True, exist_ok=True)
    job.master_path(paths).write_bytes(b"fLaC")
    job.seg_dir(paths).mkdir(exist_ok=True)
    job.listen_dir(paths).mkdir(exist_ok=True)
    for i in range(job.n_slices):
        (job.seg_dir(paths) / f"{i:04d}.opus").write_bytes(data)
    for k in range(job.n_listen):
        (job.listen_dir(paths) / f"{k:03d}.opus").write_bytes(data)
    write_meta(job.meta_path(paths), {"status": status, "spec_hash": spec or job.spec_hash,
                                      "master_hash": job.master_hash, "render_hash": job.render_hash,
                                      "lufs": lufs, "tp": -1.5, "gain_db": gain, "song": job.song_id,
                                      "variant": job.variant_id})


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.paths = Paths(work=root / "work", out=root / "out")
        # tiny settings so the fixture's 4800 samples pass the structural check
        from sfr.config import RenderSettings
        self.settings = RenderSettings(slice_s=1, listen_slice_s=1, lead_in_s=0.0, lead_out_s=0.0,
                                       sample_rate=4800, pack_size=3)
        self.song = song(D=3)
        self.variants = [sf2_variant(1), sf2_variant(2), adl_variant(58), adl_variant(0), sf2_variant(3)]

    def tearDown(self):
        self.td.cleanup()

    def jobs(self):
        out = []
        for v in self.variants:
            out.append(Job(self.song, v, self.settings, ENGINES_JSON["engines"][v["engine"]]))
        return out

    def test_validate_and_manifest(self):
        jobs = self.jobs()
        for j in jobs[:3]:
            _fake_render(j, self.paths)
        _fake_render(jobs[3], self.paths, status="failed")
        _fake_render(jobs[4], self.paths, lufs=-60)
        self.assertTrue(validate_job(jobs[0], self.paths).ok)
        self.assertEqual(validate_job(jobs[3], self.paths).reason, "failed")
        self.assertEqual(validate_job(jobs[4], self.paths).reason, "silent")
        # stale spec
        _fake_render(jobs[1], self.paths, spec="deadbeef")
        self.assertEqual(validate_job(jobs[1], self.paths).reason, "stale-spec")
        _fake_render(jobs[1], self.paths)
        # wrong sample count
        (jobs[2].seg_dir(self.paths) / "0001.opus").write_bytes(FIX.read_bytes()[:-50])
        self.assertTrue(validate_job(jobs[2], self.paths).reason.startswith("bad-ogg"))
        _fake_render(jobs[2], self.paths)

        report = build_manifests(self.paths, [self.song], self.variants, self.settings, ENGINES_JSON,
                                 {self.song["id"]: jobs}, echo=lambda *_: None)
        pub = self.paths.public
        songs_json = json.loads((pub / "songs.json").read_text())
        self.assertEqual(songs_json["schema"], 1)
        self.assertEqual(songs_json["songs"][0]["variant_count"], 3)
        self.assertEqual(songs_json["defaults"]["song"], "freedoom-e1m1")
        set_path = pub / songs_json["songs"][0]["set"].lstrip("/")
        sset = json.loads(set_path.read_text())
        self.assertEqual(sset["order"], [jobs[0].variant_id, jobs[1].variant_id, "adl-b58"])
        self.assertEqual(len(sset["groups"]), 1)
        self.assertEqual(sset["groups"][0]["variants"], sset["order"])
        self.assertEqual({e["id"] for e in sset["excluded"]}, {"adl-b0", jobs[4].variant_id})
        self.assertEqual(sset["variants"]["adl-b58"]["slot"], 2)
        self.assertEqual(sset["slices"], 3)
        gh = sset["groups"][0]["hash"]
        pk = (pub / "a" / "freedoom-e1m1" / "g" / gh / "0000.pk").read_bytes()
        self.assertEqual(len(unpack_bytes(pk)), 3)
        self.assertEqual(unpack_bytes(pk)[0], FIX.read_bytes())
        rh = sset["variants"]["adl-b58"]["render_hash"]
        self.assertTrue((pub / "a" / "freedoom-e1m1" / "l" / rh / "000.opus").exists())
        cat = json.loads((pub / songs_json["catalog"].lstrip("/")).read_text())
        self.assertEqual(len(cat["variants"]), 5)
        self.assertEqual({e["id"] for e in cat["engines"]}, {"adlmidi", "fluidsynth"})
        # idempotent: second run changes nothing
        before = sorted(str(p.relative_to(pub)) for p in pub.rglob("*") if p.is_file())
        build_manifests(self.paths, [self.song], self.variants, self.settings, ENGINES_JSON,
                        {self.song["id"]: jobs}, echo=lambda *_: None)
        after = sorted(str(p.relative_to(pub)) for p in pub.rglob("*") if p.is_file())
        self.assertEqual(before, after)
        self.assertEqual(report["songs"]["freedoom-e1m1"]["variants"], 3)

    def test_build_catalog_facets(self):
        cat = build_catalog(self.variants, ENGINES_JSON)
        comp = {f["value"]: f["count"] for f in cat["facets"]["completeness"]}
        self.assertEqual(comp, {"full_gm": 5})
        eng = {f["value"]: f["count"] for f in cat["facets"]["engine"]}
        self.assertEqual(eng, {"fluidsynth": 3, "adlmidi": 2})

    def test_build_set_shape(self):
        groups = [{"hash": "abc", "variants": ["a", "b"]}]
        meta = {"a": {"render_hash": "r1", "lufs": -20, "gain_db": 4, "tp": -1.5},
                "b": {"render_hash": "r2", "lufs": -22, "gain_db": 6, "tp": -2}}
        s = build_set(song(D=188), SETTINGS, ["a", "b"], groups, meta, [])
        self.assertEqual(s["slices"], 94)
        self.assertEqual(s["listen"]["slices"], 19)
        self.assertEqual(s["segment_samples"], 102720)
        self.assertEqual(s["variants"]["b"], {"render_hash": "r2", "lufs": -22, "gain_db": 6, "tp": -2, "group": 0, "slot": 1})


if __name__ == "__main__":
    unittest.main()
