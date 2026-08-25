"""remove_track()/prune() against a fake site bucket (issue #15). Stdlib only — boto3 and the
aws CLI are absent in CI, so publishops' S3/shell primitives are stubbed while the ordering,
the drop guard and the keep/delete decisions run for real."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("SFADMIN_BUCKET", "test")
os.environ.setdefault("SFADMIN_HOSTNAME", "test")
from sfadmin import publishops  # noqa: E402


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

    def test_prune_without_songs_json_refuses(self):
        del self.site.objects["songs.json"]
        with self.assertRaisesRegex(RuntimeError, "no songs.json"):
            publishops.prune(dry_run=False)
        self.assertNotIn("delete", self.site.events)


if __name__ == "__main__":
    unittest.main()
