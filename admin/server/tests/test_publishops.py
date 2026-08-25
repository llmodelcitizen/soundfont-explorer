"""remove_track()/prune() against a fake site bucket (issue #15). Stdlib only — boto3 and the
aws CLI are absent in CI, so publishops' S3/shell primitives are stubbed while the ordering,
the drop guard and the keep/delete decisions run for real."""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("SFADMIN_BUCKET", "test")
os.environ.setdefault("SFADMIN_HOSTNAME", "test")
from sfadmin import publishops  # noqa: E402


def _another_thread_could_lock() -> bool:
    """Could a second request's threadpool worker start its own sync/publish right now?
    Asked from another thread on purpose: OPS_LOCK is re-entrant, so the caller (which is
    inside the operation under test) already holds it and would sail straight through."""
    got: list[bool] = []

    def grab():
        ok = publishops.OPS_LOCK.acquire(timeout=0.05)
        got.append(ok)
        if ok:
            publishops.OPS_LOCK.release()

    t = threading.Thread(target=grab)
    t.start()
    t.join()
    return got[0]


class FakeCfg:
    site_bucket = "site"
    distribution = ""

    def __init__(self, repo):
        self.repo = repo


class FakeSite:
    """The site bucket as key -> bytes, and the snapshot's out/public the stubs sync into.
    `corpus` is what the box's `sfr manifest` knows: dropping an id simulates the corpus gap
    of the 2026-08-24 private-starwars incident. `events` records the order of side effects."""

    def __init__(self, repo, corpus):
        self.repo = repo
        self.corpus = set(corpus)
        self.objects: dict[str, bytes] = {}
        self.events: list[str] = []

    @property
    def public(self):
        return os.path.join(self.repo, "out", "public")

    # -- fixture helpers
    def add_song(self, sid, shash="h1", groups=("g1",), renders=("r1",)):
        doc = {"song": sid, "duration_s": 60, "order": list(renders),
               "groups": [{"hash": g} for g in groups],
               "variants": {f"v{i}": {"render_hash": r} for i, r in enumerate(renders)}}
        self.objects[f"s/{sid}/{shash}.json"] = json.dumps(doc).encode()
        for g in groups:
            self.objects[f"a/{sid}/g/{g}/0.ogg"] = b"g"
        for r in renders:
            self.objects[f"a/{sid}/l/{r}/0.ogg"] = b"l"

    def entry(self, sid, shash="h1"):
        return {"id": sid, "title": sid, "path": "", "variant_count": 1, "duration_s": 60,
                "set": f"/s/{sid}/{shash}.json"}

    def put_songs_json(self, entries):
        self.objects["songs.json"] = json.dumps({"generated_at": "t0", "songs": entries}).encode()

    def live_ids(self):
        return [s["id"] for s in json.loads(self.objects["songs.json"])["songs"]]

    def keys(self, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))

    # -- publishops primitives
    def _list(self, prefix):
        return [{"Key": k, "Size": len(self.objects[k])} for k in self.keys(prefix)]

    def _get_json(self, key):
        return json.loads(self.objects[key]) if key in self.objects else None

    def _delete_keys(self, keys):
        self.events.append("delete")
        for k in keys:
            self.objects.pop(k, None)   # S3 DeleteObjects is idempotent
        return len(keys)

    def sync_down(self):
        self.events.append("sync")
        for pre in ("s", "c"):
            shutil.rmtree(os.path.join(self.public, pre), ignore_errors=True)
        for k, v in self.objects.items():
            if k.startswith(("s/", "c/")):
                path = os.path.join(self.public, *k.split("/"))
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as fh:
                    fh.write(v)

    def _run_manifest(self):
        """--songs-json-only lists a song iff the corpus knows it AND out/public/s/<id>/
        holds a set doc (sfr.manifest._existing_set)."""
        self.events.append("manifest")
        songs = []
        for sid in sorted(self.corpus):
            d = os.path.join(self.public, "s", sid)
            docs = sorted(os.listdir(d)) if os.path.isdir(d) else []
            if docs:
                songs.append(self.entry(sid, docs[-1][:-5]))
        os.makedirs(self.public, exist_ok=True)
        with open(os.path.join(self.public, "songs.json"), "w") as fh:
            json.dump({"generated_at": "t1", "songs": songs}, fh)
        return {"songs_json": {"songs": len(songs)}}

    def _publish_songs_json(self):
        self.events.append("publish")
        with open(os.path.join(self.public, "songs.json"), "rb") as fh:
            self.objects["songs.json"] = fh.read()


class PublishOpsTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.site = FakeSite(td.name, corpus={"alpha", "beta"})
        self.site.add_song("alpha")
        self.site.add_song("beta")
        self.site.put_songs_json([self.site.entry("alpha"), self.site.entry("beta")])
        stubs = {"get_config": lambda: FakeCfg(td.name), "_expected_absent": lambda: set()}
        for name in ("_list", "_get_json", "_delete_keys", "sync_down",
                     "_run_manifest", "_publish_songs_json"):
            stubs[name] = getattr(self.site, name)
        for name, fn in stubs.items():
            p = mock.patch.object(publishops, name, fn)
            p.start()
            self.addCleanup(p.stop)

    # -- remove_track

    def test_remove_publishes_songs_json_before_deleting_objects(self):
        beta_before = self.site.keys("a/beta/") + self.site.keys("s/beta/")
        r = publishops.remove_track("alpha")
        self.assertEqual(self.site.events, ["sync", "manifest", "publish", "delete"])
        self.assertEqual(self.site.live_ids(), ["beta"])
        self.assertEqual(self.site.keys("a/alpha/") + self.site.keys("s/alpha/"), [])
        self.assertEqual(self.site.keys("a/beta/") + self.site.keys("s/beta/"), beta_before)
        self.assertEqual(r["deleted_objects"], 3)
        self.assertEqual(r["id"], "alpha")

    def test_remove_with_corpus_gap_refuses_and_touches_nothing(self):
        """The box's corpus is missing some OTHER live track, so the rebuild would drop it:
        the guard must fire before a single object of the removed track is deleted, or
        the live songs.json points at 404s and every later rebuild/prune fails too."""
        self.site.corpus.discard("beta")
        before = dict(self.site.objects)
        with self.assertRaisesRegex(RuntimeError, r"would drop live tracks \['beta'\]"):
            publishops.remove_track("alpha")
        self.assertEqual(self.site.objects, before)
        self.assertNotIn("delete", self.site.events)
        self.assertNotIn("publish", self.site.events)

    def test_remove_keeps_objects_when_publish_fails(self):
        before = dict(self.site.objects)

        def boom():
            raise RuntimeError("aws s3 cp: connection reset")

        with mock.patch.object(publishops, "_publish_songs_json", boom):
            with self.assertRaisesRegex(RuntimeError, "connection reset"):
                publishops.remove_track("alpha")
        self.assertEqual(self.site.objects, before)
        self.assertNotIn("delete", self.site.events)

    def test_remove_clears_a_track_whose_objects_are_already_gone(self):
        """The wedged state the old ordering left behind (songs.json lists a track with no
        set doc and no audio) is cleaned up by removing that track."""
        for k in self.site.keys("a/alpha/") + self.site.keys("s/alpha/"):
            del self.site.objects[k]
        r = publishops.remove_track("alpha")
        self.assertEqual(self.site.live_ids(), ["beta"])
        self.assertEqual(r["deleted_objects"], 0)

    def test_remove_refuses_a_path_like_id(self):
        for bad in ("", ".", "..", "a/b", "..\\x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                publishops.remove_track(bad)
        self.assertEqual(self.site.events, [])

    def test_remove_refuses_when_a_concurrent_sync_restores_the_set_doc(self):
        """Publishing last made the removal ride on a LOCAL edit (the rmtree of
        out/public/s/<id>), and the bucket still holds the set doc at that point. Another
        sync_down() landing before the manifest — the render finisher (renders.py finish()
        runs after the run record is already terminal, so Remove is unblocked), a second
        Remove, a /rebuild — mirrors it straight back, the manifest lists <id> again and the
        drop guard cannot see it (an expected drop that did not happen is not a drop). The
        old ordering was immune because it deleted from the bucket first; this must refuse."""
        real = self.site._run_manifest
        before = dict(self.site.objects)

        def interleaved():
            self.site.sync_down()      # the other caller's mirror lands mid-remove
            return real()

        with mock.patch.object(publishops, "_run_manifest", interleaved):
            with self.assertRaisesRegex(RuntimeError, r"still lists \['alpha'\]"):
                publishops.remove_track("alpha")
        self.assertEqual(self.site.objects, before)
        self.assertEqual(self.site.live_ids(), ["alpha", "beta"])
        self.assertNotIn("publish", self.site.events)
        self.assertNotIn("delete", self.site.events)

    def test_remove_holds_the_ops_lock_across_the_whole_operation(self):
        """Nothing else may sync/rebuild/publish while a remove is between its sync_down()
        and its delete — that window is what the test above exploits. The probes sit in the
        stubbed primitives, which take no lock of their own, so this is about remove_track's
        lock and not the one rebuild_and_publish takes around the manifest."""
        got: list[bool] = []
        sync, delete = self.site.sync_down, self.site._delete_keys

        def probing_sync():
            sync()
            got.append(_another_thread_could_lock())      # before the rmtree + manifest

        def probing_delete(keys):
            got.append(_another_thread_could_lock())      # after the publish
            return delete(keys)

        with mock.patch.object(publishops, "sync_down", probing_sync), \
                mock.patch.object(publishops, "_delete_keys", probing_delete):
            publishops.remove_track("alpha")
        self.assertEqual(got, [False, False])
        self.assertTrue(_another_thread_could_lock())     # released afterwards

    def test_remove_deletes_objects_written_during_the_rebuild(self):
        """The sync + manifest + publish take minutes; anything that lands under the track's
        prefixes in that window has to go too, so the delete set is listed after the publish."""
        real = self.site._run_manifest

        def late_write():
            r = real()
            self.site.objects["a/alpha/l/late/0.ogg"] = b"x"
            return r

        with mock.patch.object(publishops, "_run_manifest", late_write):
            r = publishops.remove_track("alpha")
        self.assertEqual(r["deleted_objects"], 4)
        self.assertEqual(self.site.keys("a/alpha/") + self.site.keys("s/alpha/"), [])

    def test_remove_says_the_publish_took_when_only_the_delete_fails(self):
        """songs.json is already live without the track: "remove failed" would send the
        operator looking for a track that is gone — only its objects are left, for prune."""
        def boom(keys):
            raise RuntimeError("AccessDenied")

        with mock.patch.object(publishops, "_delete_keys", boom):
            with self.assertRaisesRegex(RuntimeError, "no longer published.*Prune"):
                publishops.remove_track("alpha")
        self.assertEqual(self.site.live_ids(), ["beta"])

    # -- rebuild_and_publish

    def test_rebuild_refuses_when_the_live_songs_json_cannot_be_read(self):
        """A transient S3 error on the songs.json GET must not read as "nothing is live":
        that empties live_ids, disables the drop guard, and lets a corpus gap through to a
        publish and (from remove_track) a delete."""
        self.site.corpus.discard("beta")          # the gap the guard exists for
        before = dict(self.site.objects)

        def boom(key):
            raise RuntimeError("503 SlowDown")

        with mock.patch.object(publishops, "_get_json", boom):
            with self.assertRaisesRegex(RuntimeError, "SlowDown"):
                publishops.remove_track("alpha")
        self.assertEqual(self.site.objects, before)
        self.assertNotIn("publish", self.site.events)
        self.assertNotIn("delete", self.site.events)

    def test_rebuild_publishes_when_the_site_has_no_songs_json_yet(self):
        """The one benign case the guard skips: a site that has never published."""
        del self.site.objects["songs.json"]
        publishops.sync_down()
        publishops.rebuild_and_publish()
        self.assertEqual(self.site.live_ids(), ["alpha", "beta"])

    def test_resync_and_publish_holds_the_lock_across_the_pair(self):
        """/rebuild's sync + publish: a remove landing between them would rmtree a set doc
        out of the mirror this publish is about to build songs.json from — so the gap
        between the two calls has to be inside the lock too, not just each call."""
        got: list[bool] = []
        sync = self.site.sync_down

        def probing_sync():
            sync()
            got.append(_another_thread_could_lock())

        with mock.patch.object(publishops, "sync_down", probing_sync):
            publishops.resync_and_publish()
        self.assertEqual(got, [False])
        self.assertEqual(self.site.events, ["sync", "manifest", "publish"])

    # -- overview

    def test_overview_reports_a_fresh_site_but_not_a_read_failure(self):
        """With no songs.json the listings are the whole story. A transient S3 error is NOT
        that: reporting an empty live set would chip every track "discrepancy" and invite a
        Remove — which deletes its audio — so it has to surface as an error instead."""
        del self.site.objects["songs.json"]
        self.assertEqual([t["id"] for t in publishops.overview()["tracks"]], ["alpha", "beta"])

        def boom(key):
            raise RuntimeError("503 SlowDown")

        with mock.patch.object(publishops, "_get_json", boom):
            with self.assertRaisesRegex(RuntimeError, "SlowDown"):
                publishops.overview()

    # -- prune

    def test_prune_keeps_what_the_live_sets_name(self):
        self.site.objects["a/alpha/l/stale/0.ogg"] = b"x"     # superseded render
        self.site.objects["s/alpha/h0.json"] = b"{}"           # superseded set doc
        self.site.objects["a/gamma/l/r1/0.ogg"] = b"x"         # not in songs.json at all
        r = publishops.prune(dry_run=True)
        self.assertEqual(r["missing_sets"], [])
        self.assertEqual(r["doomed_objects"], 3)
        self.assertNotIn("delete", self.site.events)
        r = publishops.prune(dry_run=False)
        self.assertEqual(r["deleted"], 3)
        self.assertEqual(self.site.keys("a/") + self.site.keys("s/"),
                         ["a/alpha/g/g1/0.ogg", "a/alpha/l/r1/0.ogg", "a/beta/g/g1/0.ogg",
                          "a/beta/l/r1/0.ogg", "s/alpha/h1.json", "s/beta/h1.json"])

    def test_prune_tolerates_a_missing_set_doc(self):
        """songs.json still lists a track whose set doc is gone (the state a half-done
        remove left behind): prune must not 500, and must not guess at that track's
        audio — it keeps all of it and reports the gap."""
        del self.site.objects["s/alpha/h1.json"]
        self.site.objects["a/alpha/l/stale/0.ogg"] = b"x"
        self.site.objects["a/beta/l/stale/0.ogg"] = b"x"
        r = publishops.prune(dry_run=False)
        self.assertEqual(r["missing_sets"], ["s/alpha/h1.json"])
        self.assertEqual(r["kept_songs"], 2)
        self.assertEqual(r["deleted"], 1)
        self.assertEqual(self.site.keys("a/alpha/"),
                         ["a/alpha/g/g1/0.ogg", "a/alpha/l/r1/0.ogg", "a/alpha/l/stale/0.ogg"])
        self.assertEqual(self.site.keys("a/beta/"), ["a/beta/g/g1/0.ogg", "a/beta/l/r1/0.ogg"])

    def test_prune_keeps_a_superseded_set_doc_when_the_current_one_is_missing(self):
        """The track is "kept whole": with the current set doc gone, an older one at
        s/<id>/<oldhash>.json is the only record of which of the kept audio is current, so
        prune must not take it either (and set_docs must not drop to 0 in the overview)."""
        del self.site.objects["s/alpha/h1.json"]
        self.site.objects["s/alpha/h0.json"] = b"{}"
        r = publishops.prune(dry_run=False)
        self.assertEqual(r["missing_sets"], ["s/alpha/h1.json"])
        self.assertEqual(r["doomed_objects"], 0)
        self.assertEqual(self.site.keys("s/alpha/"), ["s/alpha/h0.json"])

    def test_prune_without_songs_json_refuses(self):
        del self.site.objects["songs.json"]
        with self.assertRaisesRegex(RuntimeError, "no songs.json"):
            publishops.prune(dry_run=False)
        self.assertNotIn("delete", self.site.events)


class StubS3:
    """Just enough of a boto3 S3 client to pin _get_json's exception handling: the real
    tests stub _get_json wholesale, so nothing else would catch `cfg.s3.exceptions.NoSuchKey`
    drifting (boto3 is absent in CI, so this is the closest a unit test gets)."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects: dict[str, bytes], error: Exception | None = None):
        self.objects = objects
        self.error = error

    def get_object(self, Bucket, Key):   # noqa: N803  (boto3's kwarg names)
        if self.error is not None:
            raise self.error
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey("The specified key does not exist.")
        return {"Body": io.BytesIO(self.objects[Key])}


class GetJsonTests(unittest.TestCase):
    def _cfg(self, s3):
        cfg = FakeCfg("/nonexistent")
        cfg.s3 = s3
        return mock.patch.object(publishops, "get_config", lambda: cfg)

    def test_get_json_reads_the_object(self):
        with self._cfg(StubS3({"songs.json": b'{"songs": []}'})):
            self.assertEqual(publishops._get_json("songs.json"), {"songs": []})
            self.assertEqual(publishops.live_songs_json(), {"songs": []})

    def test_get_json_returns_none_for_a_missing_key(self):
        with self._cfg(StubS3({})):
            self.assertIsNone(publishops._get_json("s/alpha/h1.json"))
            with self.assertRaises(publishops.NoSongsJson):
                publishops.live_songs_json()

    def test_get_json_propagates_other_errors(self):
        """A throttle/503 is not "no such object": swallowing it would make prune keep
        nothing and the drop guard see an empty live set."""
        with self._cfg(StubS3({}, error=RuntimeError("503 SlowDown"))):
            with self.assertRaisesRegex(RuntimeError, "SlowDown"):
                publishops._get_json("songs.json")


if __name__ == "__main__":
    unittest.main()
