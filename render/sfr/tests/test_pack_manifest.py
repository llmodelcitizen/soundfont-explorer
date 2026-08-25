import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from sfr.config import Paths
from sfr.jobs import Job, write_meta
from sfr import validate as validate_mod
from sfr.manifest import build_catalog, build_manifests, build_set, validate_jobs
from sfr.pack import group_hash, groups_of, header_size, pack_bytes, pack_song, unpack_bytes
from sfr.sched import WeightedSemaphore
from sfr.validate import validate_job
from .helpers import ENGINES_JSON, SETTINGS, adl_variant, sf2_variant, song

FIX = Path(__file__).parent / "fixtures" / "tone-100ms.opus"
FIX_SAMPLES = 4800   # 100 ms at 48 kHz

# Stand-in for opusdec: emits headerless s16le stereo PCM on stdout exactly like the real tool does
# for an output of "-". FAKE_OPUSDEC=short|fail selects a misbehaving decode.
FAKE_OPUSDEC = """
import os, sys
if sys.argv[-1] != "-" or not os.path.exists(sys.argv[-2]):
    sys.exit(2)
mode = os.environ.get("FAKE_OPUSDEC", "")
if mode == "fail":
    sys.stderr.write("boom: corrupt packet\\n"); sys.exit(1)
n = 4000 if mode == "short" else 4800
sys.stdout.buffer.write(bytes(n * 4))
"""


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

    def test_link_or_copy_falls_back_to_reflink_then_copy(self):
        from sfr import pack
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src.opus"
            src.write_bytes(FIX.read_bytes())
            with mock.patch.object(pack.os, "link", side_effect=OSError("EXDEV")):      # the container's bind mounts
                pack._link_or_copy(src, Path(td) / "a" / "000.opus")                    # reflink (btrfs/xfs) or copy
                with mock.patch.object(pack, "_reflink", side_effect=OSError("EOPNOTSUPP")):
                    pack._link_or_copy(src, Path(td) / "b" / "000.opus")                # plain copy
            for sub in ("a", "b"):
                self.assertEqual((Path(td) / sub / "000.opus").read_bytes(), FIX.read_bytes())
                self.assertEqual(sorted(p.name for p in (Path(td) / sub).iterdir()), ["000.opus"])   # no .tmp left

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


def _fake_render(job: Job, paths: Paths, status="ok", lufs=-21.3, gain=5.3, spec=None, master_kept=None,
                 tp=-9.0):
    """Lay down a plausible work/renders/<song>/<variant> using the fixture as every segment.
    master_kept=None leaves the key out of meta.json (a meta older than the flag)."""
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
                                      # a coherent loudness-limited render: -9.0 + 5.3 = -3.7 dBTP, under the -1.5 ceiling.
                                      # tp + gain_db is a published invariant now (#27).
                                      "lufs": lufs, "tp": tp, "gain_db": gain, "song": job.song_id,
                                      "output_tp": round(tp + gain, 3),
                                      "variant": job.variant_id,
                                      **({} if master_kept is None else {"master_kept": master_kept})})


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

    def test_a_pre_fix_work_tree_still_publishes(self):
        """Every variant left by a pre-#13 image (master_kept=false, master.flac on disk) must
        still reach songs.json. Treating that as an exclusion emptied the set and dropped the
        song from songs.json entirely, which full-run.sh then published with exit 0."""
        jobs = self.jobs()[:3]
        for j in jobs:
            _fake_render(j, self.paths, master_kept=False)
        report = build_manifests(self.paths, [self.song], self.variants, self.settings, ENGINES_JSON,
                                 {self.song["id"]: jobs}, echo=lambda *_: None, allow_partial=True)
        self.assertEqual(report["songs"]["freedoom-e1m1"]["variants"], 3)
        self.assertEqual(report["songs"]["freedoom-e1m1"]["warnings"], 3)
        songs_json = json.loads((self.paths.public / "songs.json").read_text())
        self.assertEqual([s["id"] for s in songs_json["songs"]], ["freedoom-e1m1"])

    def test_validate_rejects_a_master_the_meta_disowns(self):
        """master_kept=false next to a master.flac is what a re-render without --keep-masters
        used to leave behind: the previous spec's audio, never a re-encode source (#13)."""
        j = self.jobs()[0]
        _fake_render(j, self.paths, master_kept=False)
        v = validate_job(j, self.paths)
        # a warning, never an exclusion: a pre-fix work tree must not vanish from songs.json
        self.assertTrue(v.ok, v.reason)
        self.assertTrue(any(w.startswith("stale-master") for w in v.warnings), v.warnings)
        j.master_path(self.paths).unlink()
        self.assertTrue(validate_job(j, self.paths).ok)          # no master, none claimed
        for kept in (True, None):                                # claimed (or older than the flag): must exist
            _fake_render(j, self.paths, master_kept=kept)
            self.assertTrue(validate_job(j, self.paths).ok)
            j.master_path(self.paths).unlink()
            self.assertEqual(validate_job(j, self.paths).reason, "no-master")

    def test_manifest_for_one_song_keeps_the_others(self):
        song2 = song("joplin", D=3)
        jobs1 = self.jobs()
        jobs2 = [Job(song2, v, self.settings, ENGINES_JSON["engines"][v["engine"]]) for v in self.variants[:2]]
        for j in jobs1[:2] + jobs2:
            _fake_render(j, self.paths)
        both = [self.song, song2]
        # jobs1 is deliberately incomplete here (2 of 5 rendered): allow_partial, like a smoke run
        build_manifests(self.paths, both, self.variants, self.settings, ENGINES_JSON,
                        {"freedoom-e1m1": jobs1, "joplin": jobs2}, echo=lambda *_: None, allow_partial=True)
        pub = self.paths.public
        sj = json.loads((pub / "songs.json").read_text())
        self.assertEqual([s["id"] for s in sj["songs"]], ["freedoom-e1m1", "joplin"])
        joplin_set = sj["songs"][1]["set"]
        # now re-manifest only the first song with one more render: joplin must be untouched
        _fake_render(jobs1[2], self.paths)
        report = build_manifests(self.paths, both, self.variants, self.settings, ENGINES_JSON,
                                 {"freedoom-e1m1": jobs1}, echo=lambda *_: None, allow_partial=True)
        sj2 = json.loads((pub / "songs.json").read_text())
        self.assertEqual([s["id"] for s in sj2["songs"]], ["freedoom-e1m1", "joplin"])
        self.assertEqual(sj2["songs"][1]["set"], joplin_set)
        self.assertEqual(sj2["songs"][0]["variant_count"], 3)
        self.assertTrue((pub / joplin_set.lstrip("/")).exists())
        self.assertTrue((pub / "a" / "joplin" / "g").exists())
        self.assertIn("kept", report["songs"]["joplin"])

    def test_refuses_a_song_whose_planned_variants_never_rendered(self):
        """An interrupted render leaves planned jobs with no meta at all; the manifest must not
        turn what happens to exist into a live set (issue #11)."""
        jobs = self.jobs()
        for j in jobs[:3]:
            _fake_render(j, self.paths)
        _fake_render(jobs[3], self.paths, status="failed")     # attempted and failed: fine to exclude
        # jobs[4] never ran
        lines = []
        report = build_manifests(self.paths, [self.song], self.variants, self.settings, ENGINES_JSON,
                                 {self.song["id"]: jobs}, echo=lines.append)
        pub = self.paths.public
        self.assertEqual(report["refused"], ["freedoom-e1m1"])
        self.assertEqual(report["songs"]["freedoom-e1m1"]["never_rendered"], [jobs[4].variant_id])
        self.assertIn("1 of 5 planned variants never rendered", report["songs"]["freedoom-e1m1"]["refused"])
        self.assertTrue(any("REFUSED" in ln for ln in lines))
        self.assertFalse((pub / "s" / "freedoom-e1m1").exists())            # no set document written
        self.assertEqual(json.loads((pub / "songs.json").read_text())["songs"], [])   # nothing to publish
        # the deliberate-subset escape hatch (smoke runs) still builds the set from what exists
        report = build_manifests(self.paths, [self.song], self.variants, self.settings, ENGINES_JSON,
                                 {self.song["id"]: jobs}, echo=lambda *_: None, allow_partial=True)
        self.assertNotIn("refused", report)
        self.assertEqual(report["songs"]["freedoom-e1m1"]["variants"], 3)
        published = json.loads((pub / "songs.json").read_text())["songs"][0]["set"]
        # once a set exists, a later refused rebuild keeps it rather than dropping the song
        (jobs[0].meta_path(self.paths)).unlink()
        report = build_manifests(self.paths, [self.song], self.variants, self.settings, ENGINES_JSON,
                                 {self.song["id"]: jobs}, echo=lambda *_: None)
        self.assertEqual(report["refused"], ["freedoom-e1m1"])
        self.assertEqual(report["songs"]["freedoom-e1m1"]["kept"], published)
        self.assertEqual(json.loads((pub / "songs.json").read_text())["songs"][0]["set"], published)

    def test_pack_song_skips_by_size_without_rereading_members(self):
        jobs = self.jobs()[:3]
        for j in jobs:
            _fake_render(j, self.paths)
        args = ("freedoom-e1m1", self.variants[:3], {j.variant_id: j.out_dir(self.paths) for j in jobs},
                {j.variant_id: j.render_hash for j in jobs}, jobs[0].n_slices, jobs[0].n_listen, self.paths.public, 3)
        real, reads = Path.read_bytes, []

        def counting(self):
            reads.append(self)
            return real(self)
        with mock.patch.object(Path, "read_bytes", counting):
            first = pack_song(*args)
            self.assertEqual(len(reads), 3 * jobs[0].n_slices)        # every member once
            self.assertEqual(pack_song(*args), first)
            self.assertEqual(len(reads), 3 * jobs[0].n_slices)        # second run: sizes matched, nothing read
        gdir = self.paths.public / "a" / "freedoom-e1m1" / "g" / first["groups"][0]["hash"]
        self.assertEqual(unpack_bytes((gdir / "0001.pk").read_bytes()), [FIX.read_bytes()] * 3)
        ldir = self.paths.public / "a" / "freedoom-e1m1" / "l" / jobs[1].render_hash
        self.assertEqual([p.name for p in sorted(ldir.iterdir())], [f"{k:03d}.opus" for k in range(jobs[1].n_listen)])
        self.assertEqual((ldir / "000.opus").read_bytes(), FIX.read_bytes())

    def test_thorough_decodes_through_a_pipe(self):
        jobs = self.jobs()[:1]
        _fake_render(jobs[0], self.paths)
        fake = Path(self.td.name) / "fake_opusdec.py"
        fake.write_text(FAKE_OPUSDEC)
        before = set(self.paths.work.rglob("*"))
        with mock.patch.object(validate_mod, "OPUSDEC", [sys.executable, str(fake)]):
            v = validate_job(jobs[0], self.paths, thorough=True)
            self.assertTrue(v.ok, v.reason)
            self.assertEqual(v.checked, jobs[0].n_slices + jobs[0].n_listen)
            with mock.patch.dict(os.environ, {"FAKE_OPUSDEC": "short"}):
                self.assertEqual(validate_job(jobs[0], self.paths, thorough=True).reason,
                                 "decoded-samples:0000.opus:4000!=4800")
            with mock.patch.dict(os.environ, {"FAKE_OPUSDEC": "fail"}):
                self.assertEqual(validate_job(jobs[0], self.paths, thorough=True).reason,
                                 "decode:0000.opus:opusdec rc=1 boom: corrupt packet")
        # nothing was written anywhere under work/ by validation (it decodes through a pipe)
        self.assertEqual(set(self.paths.work.rglob("*")), before)

    @unittest.skipUnless(shutil.which("opusdec"), "opusdec not installed (runs inside the sfr-render image)")
    def test_thorough_with_real_opusdec(self):
        jobs = self.jobs()[:1]
        _fake_render(jobs[0], self.paths)
        self.assertEqual(validate_mod._decoded_samples(FIX), FIX_SAMPLES)
        v = validate_job(jobs[0], self.paths, thorough=True)
        self.assertTrue(v.ok, v.reason)
        (jobs[0].seg_dir(self.paths) / "0002.opus").write_bytes(b"OggS" + FIX.read_bytes()[4:-7])
        self.assertTrue(validate_job(jobs[0], self.paths, thorough=True).reason.startswith("bad-ogg:0002.opus"))

    def test_parallel_validation_keeps_job_order_and_hashes(self):
        # 9 variants, three of them excluded in known positions; the parallel and serial runs must
        # produce byte-identical set documents (excluded[] order is part of the content hash)
        self.variants = [sf2_variant(n) for n in range(1, 8)] + [adl_variant(58), adl_variant(0)]
        jobs = self.jobs()
        for i, j in enumerate(jobs):
            if i == 1:
                _fake_render(j, self.paths, status="failed")
            elif i == 4:
                _fake_render(j, self.paths, lufs=-60)
            elif i == 8:
                _fake_render(j, self.paths, spec="deadbeef")
            else:
                _fake_render(j, self.paths)
        verdicts = validate_jobs(jobs, self.paths, thorough=False, workers=8)
        self.assertEqual([v.ok for v in verdicts], [i not in (1, 4, 8) for i in range(9)])
        docs = []
        for workers in (1, 8):
            build_manifests(self.paths, [self.song], self.variants, self.settings, ENGINES_JSON,
                            {self.song["id"]: jobs}, workers=workers, echo=lambda *_: None)
            sj = json.loads((self.paths.public / "songs.json").read_text())
            docs.append((self.paths.public / sj["songs"][0]["set"].lstrip("/")).read_bytes())
        self.assertEqual(docs[0], docs[1])
        sset = json.loads(docs[1])
        self.assertEqual([e["id"] for e in sset["excluded"]],
                         [jobs[1].variant_id, jobs[4].variant_id, jobs[8].variant_id])
        self.assertEqual([e["reason"] for e in sset["excluded"]], ["failed", "silent", "stale-spec"])
        self.assertEqual(len(sset["order"]), 6)

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
