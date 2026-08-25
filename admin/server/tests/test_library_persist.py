"""_persist_canon_products (#16): a --only run never prunes canon/rendered/, and a persist
is refused when the local songs.json lost tracks the bucket copy has and the library still
expects (a stale songs/ tree: failed restore, bundle stub). Stdlib only — the S3 client and
the aws CLI are stubbed."""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("SFADMIN_BUCKET", "test")
os.environ.setdefault("SFADMIN_HOSTNAME", "test")
from sfadmin import library  # noqa: E402


class FakeS3:
    """The calls the canon-run path makes, shaped like a boto3 client."""

    class exceptions:  # noqa: N801 - boto exposes them as client.exceptions.*
        class NoSuchKey(Exception):
            pass

        class ClientError(Exception):
            pass

    def __init__(self, canon_songs_json):
        self.canon_songs_json = canon_songs_json   # None: nothing persisted yet
        self.puts = []

    def get_object(self, Bucket, Key):
        assert Key == "canon/songs.json", Key
        if self.canon_songs_json is None:
            raise self.exceptions.NoSuchKey()
        return {"Body": io.BytesIO(json.dumps(self.canon_songs_json).encode())}

    def put_object(self, **kw):
        self.puts.append(kw["Key"])
        return {"ETag": "etag"}


def entry(sid, status="ok", hidden=False):
    return {"id": sid, "path": sid + ".mid", "hidden": hidden,
            "canon": {"status": status, "reason": None, "canonical_sha256": None,
                      "duration_s": None, "checked_at": None}}


def songs_doc(ids):
    return {"songs": [{"id": i, "sha256": "0" * 64, "duration_s": 1} for i in ids]}


class PersistBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.songs = os.path.join(self.tmp, "songs")
        os.makedirs(os.path.join(self.songs, "rendered"))
        self.runs = []
        p = mock.patch.object(library.subprocess, "run", side_effect=self._run)
        p.start()
        self.addCleanup(p.stop)
        self.lib = library.Library.__new__(library.Library)   # no get_config / boto3
        self.lib.lock = threading.RLock()
        self.lib.etag = None
        self.lib.doc = {"schema": 1, "updated_at": None, "entries": {}}

    def _run(self, cmd, **kw):
        self.runs.append(list(cmd))
        return types.SimpleNamespace(returncode=0, stderr="")

    def box(self, local, bucket, entries):
        """A box: ids in the local songs.json, ids in the bucket's canon/songs.json
        (None = never persisted), library entries."""
        with open(os.path.join(self.songs, "songs.json"), "w") as fh:
            json.dump(songs_doc(local), fh)
        self.s3 = FakeS3(None if bucket is None else songs_doc(bucket))
        self.lib.cfg = types.SimpleNamespace(
            repo=self.tmp, bucket="b", s3=self.s3,
            library_json=os.path.join(self.tmp, "library.json"))
        self.lib.doc["entries"] = {e["id"]: e for e in entries}

    def aws_runs(self):
        return [c for c in self.runs if c[0] == "aws"]

    def sync_cmd(self):
        syncs = [c for c in self.runs if c[:3] == ["aws", "s3", "sync"]]
        self.assertEqual(len(syncs), 1, self.runs)
        return syncs[0]


class PersistTests(PersistBase):
    def test_only_run_never_prunes_the_bucket(self):
        self.box(["a", "b"], ["a", "b"], [entry("a"), entry("b")])
        self.assertTrue(self.lib._persist_canon_products(full_run=False))
        self.assertNotIn("--delete", self.sync_cmd())

    def test_full_run_prunes_stale_keys(self):
        self.box(["a", "b"], ["a", "b"], [entry("a"), entry("b")])
        self.assertTrue(self.lib._persist_canon_products(full_run=True))
        cmd = self.sync_cmd()
        self.assertIn("--delete", cmd)
        self.assertEqual(cmd[3:5], [os.path.join(self.songs, "rendered") + "/",
                                    "s3://b/canon/rendered/"])
        self.assertIn(["aws", "s3", "cp", os.path.join(self.songs, "songs.json"),
                       "s3://b/canon/songs.json", "--only-show-errors"], self.runs)

    def test_stale_base_is_refused_before_anything_is_uploaded(self):
        # the issue's scenario: the restore never happened, songs.json is the bundle's stub
        # and a targeted run merged one track into it
        self.box(["stub", "x"], ["a", "b", "c", "x"], [entry(i) for i in "abcx"])
        with self.assertRaises(RuntimeError) as cm:
            self.lib._persist_canon_products(full_run=False)
        self.assertIn("lacks 3 tracks", str(cm.exception))
        self.assertIn("a, b, c", str(cm.exception))
        self.assertEqual(self.runs, [])

    def test_drops_the_library_explains_persist(self):
        # deleted (no entry), hidden and refused tracks may vanish from songs.json
        self.box(["a"], ["a", "deleted", "hid", "ref"],
                 [entry("a"), entry("hid", hidden=True), entry("ref", status="refused")])
        self.assertTrue(self.lib._persist_canon_products(full_run=True))
        self.assertIn("--delete", self.sync_cmd())

    def test_first_persist_has_no_bucket_copy_to_check(self):
        self.box(["a"], None, [entry("a"), entry("b")])
        self.assertTrue(self.lib._persist_canon_products(full_run=False))
        self.assertEqual(len(self.aws_runs()), 2)   # sync + songs.json

    def test_transfer_failure_is_reported_not_raised(self):
        # unchanged behaviour: only a stale tree refuses; an aws error is WARN + False
        self.box(["a"], ["a"], [entry("a")])
        self.runs = None
        with mock.patch.object(library.subprocess, "run",
                               side_effect=library.subprocess.CalledProcessError(1, "aws")):
            with mock.patch("builtins.print"):
                self.assertFalse(self.lib._persist_canon_products(full_run=True))


class BlockersTests(unittest.TestCase):
    def test_pending_track_the_bucket_has_must_still_be_local(self):
        # edited since its last build (pending) but persisted before: a --only run's merge
        # keeps it, so its absence means the merge base was stale ...
        entries = {"p": entry("p", status="pending"), "n": entry("n", status="pending")}
        self.assertEqual(library.canon_persist_blockers({"x"}, {"p", "x"}, entries), ["p"])
        # ... while a fresh upload the bucket never had is not a blocker
        self.assertEqual(library.canon_persist_blockers({"x"}, {"x"}, entries), [])

    def test_sorted_with_the_explained_drops_exempt(self):
        entries = {"b": entry("b"), "a": entry("a"), "h": entry("h", hidden=True),
                   "r": entry("r", status="refused"), "u": entry("u", status="unparsed")}
        got = library.canon_persist_blockers(set(), {"b", "a", "h", "r", "u", "gone"}, entries)
        self.assertEqual(got, ["a", "b"])


class CanonRunTests(PersistBase):
    """canon_run end to end with fragment.py/canon.py stubbed: the run kind reaches the
    persist step, and a refusal surfaces as the run's error."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.songs, "canon-report.json"), "w") as fh:
            json.dump({"refused": []}, fh)

    def test_targeted_run_syncs_without_delete(self):
        self.box(["a", "x"], ["a", "x"], [entry("a"), entry("x", status="pending")])
        r = self.lib.canon_run(only=["x"])
        self.assertTrue(r["persisted"])
        self.assertNotIn("--delete", self.sync_cmd())
        self.assertEqual(r["ran"]["x"]["status"], "ok")
        self.assertEqual(self.s3.puts, ["library/library.json"])

    def test_full_run_syncs_with_delete(self):
        self.box(["a", "x"], ["a", "x"], [entry("a"), entry("x")])
        r = self.lib.canon_run()
        self.assertTrue(r["persisted"])
        self.assertIn("--delete", self.sync_cmd())

    def test_targeted_run_over_a_stale_tree_refuses_to_persist(self):
        self.box(["stub", "x"], ["a", "b", "c", "x"], [entry(i) for i in "abcx"])
        with self.assertRaisesRegex(RuntimeError, "lacks 3 tracks"):
            self.lib.canon_run(only=["x"])
        self.assertEqual(self.aws_runs(), [])
        # the run's own results were still folded into library.json
        self.assertEqual(self.s3.puts, ["library/library.json"])


if __name__ == "__main__":
    unittest.main()
