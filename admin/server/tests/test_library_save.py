"""Library._save(): every PUT of library.json is conditional (#19). Stdlib only — the S3
client is a fake installed through config._client."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("SFADMIN_BUCKET", "test")
os.environ.setdefault("SFADMIN_HOSTNAME", "test")
from sfadmin import config, library  # noqa: E402


class FakeClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeExceptions:
    ClientError = FakeClientError


class FakeS3:
    """Just enough of put_object/get_object to exercise the precondition paths."""
    exceptions = FakeExceptions()

    def __init__(self, stored: dict | None = None, etag: str = '"s3-etag"'):
        self.stored = stored      # None = no library.json in the bucket
        self.etag = etag
        self.puts: list[dict] = []
        self.get_fails = False

    def get_object(self, Bucket, Key):
        if self.get_fails or self.stored is None:
            raise FakeClientError("NoSuchKey")
        return {"Body": _Body(self.stored), "ETag": self.etag}

    def put_object(self, **kw):
        self.puts.append(kw)
        exists = self.stored is not None
        if kw.get("IfNoneMatch") == "*" and exists:
            raise FakeClientError("PreconditionFailed")
        if "IfMatch" in kw and (not exists or kw["IfMatch"] != self.etag):
            raise FakeClientError("PreconditionFailed")
        self.stored = json.loads(kw["Body"])
        self.etag = '"new-etag"'
        return {"ETag": self.etag}


class _Body:
    def __init__(self, doc):
        self._data = json.dumps(doc).encode()

    def read(self):
        return self._data


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.data, "library"))
        config.get_config.cache_clear()
        self.env = mock.patch.dict(os.environ, {"SFADMIN_BUCKET": "b", "SFADMIN_HOSTNAME": "h",
                                                "SFADMIN_DATA": self.data})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(config.get_config.cache_clear)

    def make(self, s3: FakeS3) -> library.Library:
        self.patch = mock.patch.object(config, "_client", lambda service: s3)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        return library.Library()

    def test_precondition_helper(self):
        self.assertEqual(library.put_precondition('"e"'), {"IfMatch": '"e"'})
        self.assertEqual(library.put_precondition(None), {"IfNoneMatch": "*"})

    def test_loaded_from_s3_puts_with_ifmatch(self):
        s3 = FakeS3(stored={"schema": 1, "updated_at": "t0", "entries": {}})
        lib = self.make(s3)
        self.assertEqual(lib.etag, '"s3-etag"')
        lib.doc["updated_at"] = "t1"
        lib._save()
        self.assertEqual(s3.puts[-1]["IfMatch"], '"s3-etag"')
        self.assertNotIn("IfNoneMatch", s3.puts[-1])
        self.assertEqual(lib.etag, '"new-etag"')

    def test_mirror_fallback_never_overwrites_an_existing_s3_copy(self):
        # boot: S3 unreadable, the local mirror (older) is loaded with no ETag
        with open(os.path.join(self.data, "library", "library.json"), "w") as fh:
            json.dump({"schema": 1, "updated_at": "old", "entries": {}}, fh)
        s3 = FakeS3(stored={"schema": 1, "updated_at": "newer", "entries": {"x": 1}})
        s3.get_fails = True
        lib = self.make(s3)
        self.assertIsNone(lib.etag)
        self.assertEqual(lib.doc["updated_at"], "old")
        s3.get_fails = False  # S3 is back by the time the admin edits something
        lib.doc["updated_at"] = "edited"
        with self.assertRaises(library.ConflictError) as cm:
            lib._save()
        self.assertEqual(s3.puts[-1]["IfNoneMatch"], "*")            # never unconditional
        self.assertEqual(s3.stored["updated_at"], "newer")             # S3 copy untouched
        self.assertEqual(lib.doc["updated_at"], "newer")               # resynced to S3
        self.assertEqual(lib.etag, '"s3-etag"')
        self.assertIn("local mirror", str(cm.exception))
        lib.doc["updated_at"] = "edited"                               # retry now succeeds
        lib._save()
        self.assertEqual(s3.puts[-1]["IfMatch"], '"s3-etag"')
        self.assertEqual(s3.stored["updated_at"], "edited")

    def test_empty_bucket_first_save_creates(self):
        s3 = FakeS3(stored=None)
        lib = self.make(s3)
        self.assertIsNone(lib.etag)
        lib.doc["updated_at"] = "first"
        lib._save()
        self.assertEqual(s3.puts[-1]["IfNoneMatch"], "*")
        self.assertEqual(s3.stored["updated_at"], "first")
        self.assertEqual(lib.etag, '"new-etag"')
        with open(os.path.join(self.data, "library", "library.json")) as fh:
            self.assertEqual(json.load(fh)["updated_at"], "first")   # mirror written after S3


class CanonRunReportTests(unittest.TestCase):
    """canon_run() hands the SPA what a targeted run cost. canon.py prints the songs it
    dropped from songs.json, but library deliberately inherits its stdout (progress lines
    go to the journal live), so the operator only ever saw that in `journalctl` — and
    otherwise learned of it from the next failed "Republish songs.json" (#19)."""

    def repo_with_report(self, report: dict, built: list[str]) -> str:
        repo = tempfile.mkdtemp()
        os.makedirs(os.path.join(repo, "songs"))
        with open(os.path.join(repo, "songs", "canon-report.json"), "w") as fh:
            json.dump(report, fh)
        with open(os.path.join(repo, "songs", "songs.json"), "w") as fh:
            json.dump({"songs": [{"id": s, "sha256": "x", "duration_s": 1} for s in built]}, fh)
        return repo

    def run_canon(self, repo: str, entries: list[str], only: list[str] | None):
        data = tempfile.mkdtemp()
        os.makedirs(os.path.join(data, "library"))
        s3 = FakeS3(stored={"schema": 1, "updated_at": "t0", "entries": {
            e: {"id": e, "hidden": False, "canon": {"status": "ok", "reason": None,
                                                    "canonical_sha256": None, "duration_s": None,
                                                    "checked_at": None}} for e in entries}})
        config.get_config.cache_clear()
        self.addCleanup(config.get_config.cache_clear)
        with mock.patch.dict(os.environ, {"SFADMIN_BUCKET": "b", "SFADMIN_HOSTNAME": "h",
                                          "SFADMIN_DATA": data, "SFADMIN_REPO": repo}), \
                mock.patch.object(config, "_client", lambda service: s3), \
                mock.patch.object(library.subprocess, "run",
                                  return_value=mock.Mock(returncode=0, stderr="")), \
                mock.patch.object(library.Library, "_persist_canon_products", lambda _self, full_run=True: True):
            return library.Library().canon_run(only)

    def test_a_dropped_song_is_reported_to_the_caller(self):
        repo = self.repo_with_report({"schema": 1, "refused": [], "dropped": ["gone"]},
                                     built=["kept"])
        out = self.run_canon(repo, ["kept", "gone"], only=["gone"])
        self.assertEqual(out["dropped"], ["gone"])

    def test_nothing_dropped_says_nothing(self):
        repo = self.repo_with_report({"schema": 1, "refused": [], "dropped": []},
                                     built=["kept"])
        out = self.run_canon(repo, ["kept"], only=["kept"])
        self.assertNotIn("dropped", out)

    def test_a_report_without_the_field_is_fine(self):
        # canon-report.json restored from the bucket may predate the field
        repo = self.repo_with_report({"schema": 1, "refused": []}, built=["kept"])
        out = self.run_canon(repo, ["kept"], only=["kept"])
        self.assertNotIn("dropped", out)


if __name__ == "__main__":
    unittest.main()
