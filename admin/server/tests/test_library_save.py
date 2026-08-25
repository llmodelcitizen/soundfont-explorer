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


if __name__ == "__main__":
    unittest.main()
