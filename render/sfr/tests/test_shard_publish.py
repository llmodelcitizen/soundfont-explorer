"""render/cloud/shard.py publish path: per-song uploads and the bounded pool (#25).

The behaviour this pins is the 2026-08-25 tail: one song at a time, each one re-syncing the
whole accumulated a/ and s/ trees, on a host with 96 idle cores. It must also keep #12's
guarantee that no song can go unpublished without landing in `failed`.
"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest

CLOUD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cloud")
shard = None   # bound by setUpModule
_saved_env = {}


def setUpModule():
    """shard.py reads its buckets and allocation from the environment at import (see its own
    module docstring), so the environment has to be in place before the import."""
    global shard
    for k, v in (("SFR_FONTS_BUCKET", "fonts"), ("SFR_SITE_BUCKET", "site"),
                 ("SFR_WORKERS", "96"), ("SFR_MEM_UNITS", "64")):
        _saved_env[k] = os.environ.get(k)
        os.environ[k] = v
    sys.path.insert(0, CLOUD)
    import shard as _shard
    shard = _shard


def tearDownModule():
    if CLOUD in sys.path:
        sys.path.remove(CLOUD)
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class SyncCommandTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.public = pathlib.Path(self.td.name) / "public"
        for rel in ("a/song-one", "a/song-two", "s/song-one", "s/song-two", "c"):
            (self.public / rel).mkdir(parents=True)
        self.addCleanup(self.td.cleanup)

    def test_a_song_uploads_only_its_own_subtree(self):
        cmds = shard.sync_commands(self.public, "site", song="song-one")
        srcs = [c[-2] for c in cmds]
        dsts = [c[-1] for c in cmds]
        self.assertTrue(all("song-two" not in x for x in srcs + dsts), srcs + dsts)
        self.assertIn(f"{self.public}/a/song-one/", srcs)
        self.assertIn(f"{self.public}/s/song-one/", srcs)
        self.assertIn("s3://site/a/song-one/", dsts)
        self.assertIn("s3://site/s/song-one/", dsts)

    def test_the_shared_catalog_is_never_song_scoped(self):
        cmds = shard.sync_commands(self.public, "site", song="song-one")
        cat = [c for c in cmds if c[-1] == "s3://site/c/"]
        self.assertEqual(len(cat), 1, cmds)
        self.assertEqual(cat[0][-2], f"{self.public}/c/")

    def test_every_upload_still_stamps_the_headers_from_sfr_publish(self):
        for cmd in shard.sync_commands(self.public, "site", song="song-one"):
            self.assertIn("--cache-control", cmd)
            self.assertIn(shard.IMMUTABLE, cmd)
            self.assertIn("--content-type", cmd)
            self.assertIn("--size-only", cmd)

    def test_transfer_concurrency_is_explicit_when_asked_for(self):
        plain = shard.sync_commands(self.public, "site", song="song-one")
        tuned = shard.sync_commands(self.public, "site", song="song-one", workers=32)
        self.assertNotIn("--numworkers", plain[0])
        # a global flag, so it has to land before the subcommand
        i = tuned[0].index("--numworkers")
        self.assertEqual(tuned[0][i + 1], "32")
        self.assertLess(i, tuned[0].index("sync"))

    def test_uploads_do_not_log_a_line_per_object(self):
        """s5cmd's default `info` level made 96.6% of a shard's CloudWatch stream per-object
        receipts, which is what made the Logs view unreadable and slow to fetch."""
        for cmd in shard.sync_commands(self.public, "site", song="song-one"):
            i = cmd.index("--log")
            self.assertEqual(cmd[i + 1], "error")
            self.assertLess(i, cmd.index("sync"))

    def test_without_a_song_it_still_covers_the_whole_tree(self):
        dsts = [c[-1] for c in shard.sync_commands(self.public, "site")]
        self.assertIn("s3://site/a/", dsts)
        self.assertIn("s3://site/s/", dsts)

    def test_a_missing_subtree_produces_no_command(self):
        self.assertEqual(shard.sync_commands(self.public, "site", song="no-such-song"),
                         [c for c in shard.sync_commands(self.public, "site", song="no-such-song")
                          if c[-1] == "s3://site/c/"])


class PublisherPoolTests(unittest.TestCase):
    """The pool must overlap songs, and must never lose one silently (#12)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.work = pathlib.Path(self.td.name)
        self._work, shard.WORK = shard.WORK, self.work
        self._pub, self._exp = shard.publish_song, shard.expected
        self.addCleanup(self.restore)

    def restore(self):
        shard.WORK, shard.publish_song, shard.expected = self._work, self._pub, self._exp

    def ready(self, *songs):
        for s in songs:
            d = self.work / "renders" / s / "v1"
            d.mkdir(parents=True, exist_ok=True)
            (d / "meta.json").write_text("{}")

    def run_publisher(self, songs):
        done = threading.Event()
        done.set()                                     # renderer already finished
        state = {"left": list(songs), "failed": []}
        shard.publisher(songs, [], done, state)
        return state

    def test_songs_publish_concurrently_not_one_after_another(self):
        self.ready("a", "b", "c", "d")
        shard.expected = lambda song, sel: 1
        live, peak = [], [0]
        lock = threading.Lock()

        def slow(song, partial_ok=False):
            with lock:
                live.append(song)
                peak[0] = max(peak[0], len(live))
            time.sleep(0.15)
            with lock:
                live.remove(song)
            return True
        shard.publish_song = slow
        t0 = time.monotonic()
        state = self.run_publisher(["a", "b", "c", "d"])
        elapsed = time.monotonic() - t0
        self.assertEqual(state["failed"], [])
        self.assertGreater(peak[0], 1, "publishes ran one at a time")
        self.assertLess(elapsed, 0.15 * 4, "no overlap at all")

    def test_a_failing_song_is_reported_and_the_others_still_publish(self):
        self.ready("a", "b", "c")
        shard.expected = lambda song, sel: 1
        shard.publish_song = lambda song, partial_ok=False: song != "b"
        state = self.run_publisher(["a", "b", "c"])
        self.assertEqual(state["failed"], ["b"])
        self.assertEqual(state["left"], [])

    def test_a_raising_song_is_reported_not_swallowed(self):
        self.ready("a", "b")
        shard.expected = lambda song, sel: 1

        def boom(song, partial_ok=False):
            if song == "a":
                raise RuntimeError("s5cmd exploded")
            return True
        shard.publish_song = boom
        state = self.run_publisher(["a", "b"])
        self.assertEqual(state["failed"], ["a"])

    def test_a_dead_publisher_marks_everything_it_had_left(self):
        self.ready("a", "b")

        def boom(song, sel):
            raise RuntimeError("plan reports 0 jobs")
        shard.expected = boom
        state = self.run_publisher(["a", "b"])
        self.assertEqual(sorted(state["failed"]), ["a", "b"])
        self.assertEqual(state["left"], [])

    def test_the_pool_is_drained_before_the_publisher_returns(self):
        """main() judges the shard the moment this returns: a future still running then would be
        killed at process exit with the song neither published nor in `failed` (#12)."""
        self.ready("a", "b", "c")
        shard.expected = lambda song, sel: 1
        finished = []
        shard.publish_song = lambda song, partial_ok=False: (time.sleep(0.1), finished.append(song), True)[-1]
        state = self.run_publisher(["a", "b", "c"])
        self.assertEqual(sorted(finished), ["a", "b", "c"])
        self.assertEqual(state["failed"], [])


class FailureClassificationTests(unittest.TestCase):
    """A shard's exit code is its verdict on itself. `sfr render` exits 1 if any single job
    failed, so the shard re-judges from the metas — and it has to know which failures are
    ordinary exclusions.

    The live symptom: 5 of the 8 shards on the 2026-08-25 run reported FAILED to Batch after
    publishing every song they were given, because #27's new `peak-unsafe` reason was not on
    the list and 9-24 variants per shard hit it."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self._work, shard.WORK = shard.WORK, pathlib.Path(self.td.name)
        self.addCleanup(lambda: setattr(shard, "WORK", self._work))

    def meta(self, song, variant, **kw):
        d = shard.WORK / "renders" / song / variant
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({"variant": variant, **kw}))

    def test_a_peak_unsafe_variant_is_an_exclusion_not_a_broken_shard(self):
        self.meta("s1", "adl-b11-opl2", status="failed", reason="peak-unsafe", output_tp=-1.31)
        bad, excluded = shard.failure_report(["s1"])
        self.assertEqual(bad, [])
        self.assertEqual([r[1] for r in excluded["peak-unsafe"]], ["adl-b11-opl2"])

    def test_silent_is_still_an_exclusion(self):
        self.meta("s1", "sf2-aaa", status="failed", reason="silent")
        bad, excluded = shard.failure_report(["s1"])
        self.assertEqual(bad, [])
        self.assertEqual(len(excluded["silent"]), 1)

    def test_a_real_failure_still_fails_the_shard(self):
        self.meta("s1", "sf2-bbb", status="failed", reason="exit")
        self.meta("s1", "sf2-ccc", status="failed", reason="timeout")
        bad, excluded = shard.failure_report(["s1"])
        self.assertEqual(sorted(r[2] for r in bad), ["exit", "timeout"])
        self.assertEqual(excluded, {})

    def test_successful_renders_are_not_reported_at_all(self):
        self.meta("s1", "sf2-ok", status="ok")
        self.assertEqual(shard.failure_report(["s1"]), ([], {}))

    def test_the_worst_overshoot_is_spelled_out(self):
        """Whether the ceiling is a hair tight or a render is 30 dB hot is the whole question,
        and the log has to answer it without a re-run."""
        for i, tp in enumerate((-1.49, -1.31, -1.402)):
            self.meta("s1", f"adl-{i}", status="failed", reason="peak-unsafe", output_tp=tp)
        self.meta("s1", "sf2-q", status="failed", reason="silent")
        line = shard.excluded_line(shard.failure_report(["s1"])[1])
        self.assertIn("3 peak-unsafe", line)
        self.assertIn("worst -1.310 dBTP", line)
        self.assertIn(f"{shard.TP_CEILING_DBTP} ceiling", line)
        self.assertIn("1 silent", line)

    def test_an_exclusion_with_no_measurement_still_counts(self):
        self.meta("s1", "sf2-q", status="failed", reason="silent")
        self.assertEqual(shard.excluded_line(shard.failure_report(["s1"])[1]), "1 silent")


class PhaseTests(unittest.TestCase):
    def test_the_summary_reports_the_tail_as_a_fraction_of_the_shard(self):
        p = shard.Phases()
        p.started = time.monotonic() - 1000
        p.stage_s, p.render_s = 40.0, 700.0
        p.record("a", manifest_s=30.0, upload_s=90.0, variants=560)
        p.record("b", manifest_s=25.0, upload_s=80.0, variants=540)
        out = p.summary(published=2, failed=0)
        self.assertAlmostEqual(out["post_render_tail_s"], 260.0, delta=2)
        self.assertAlmostEqual(out["tail_fraction"], 0.26, delta=0.01)
        self.assertEqual(out["manifest_s_total"], 55.0)
        self.assertEqual(out["upload_s_total"], 170.0)
        self.assertEqual(out["songs_published"], 2)
        for knob in ("publish_pool", "manifest_workers", "upload_workers", "render_workers"):
            self.assertIn(knob, out)

    def test_the_knobs_scale_with_the_allocation(self):
        self.assertGreaterEqual(shard.PUBLISH_POOL, 2)
        self.assertLessEqual(shard.PUBLISH_POOL, 8)
        self.assertGreaterEqual(shard.MANIFEST_WORKERS, 1)
        # the whole point: publishing must not be a single stream on a 96-core host
        self.assertGreater(shard.PUBLISH_POOL * shard.MANIFEST_WORKERS, 1)


if __name__ == "__main__":
    unittest.main()
