"""Editing a library entry's licence. Stdlib only — the S3 client is a fake through config._client.

A library entry is the only way a MIDI becomes a song, so `license` is entry metadata now and
the admin can set it. It is checked here rather than at canon time because canon refusing an
unknown key looks like a track quietly missing from the site, while a rejected edit says why.
"""
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
from sfadmin.entries import canon_state, new_entry  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))


class FakeS3:
    class exceptions:
        class ClientError(Exception):
            response = {"Error": {"Code": "x"}}

    def __init__(self, doc):
        self.stored = doc

    def get_object(self, Bucket, Key):
        class Body:
            def __init__(self, d):
                self._d = json.dumps(d).encode()

            def read(self):
                return self._d
        return {"Body": Body(self.stored), "ETag": '"e"'}

    def put_object(self, **kw):
        self.stored = json.loads(kw["Body"])
        return {"ETag": '"e"'}


class LicenseEditTests(unittest.TestCase):
    def setUp(self):
        data = tempfile.mkdtemp()
        os.makedirs(os.path.join(data, "library"))
        entry = new_entry("a-one", "a/one.mid", "one", sha256="ab" * 32, size=1,
                          added_at="2026-08-25T00:00:00Z", canon=canon_state("ok"))
        self.s3 = FakeS3({"schema": 1, "updated_at": "t0", "entries": {"a-one": entry}})
        config.get_config.cache_clear()
        self.addCleanup(config.get_config.cache_clear)
        env = mock.patch.dict(os.environ, {"SFADMIN_BUCKET": "b", "SFADMIN_HOSTNAME": "h",
                                           "SFADMIN_DATA": data, "SFADMIN_REPO": REPO})
        env.start()
        self.addCleanup(env.stop)
        patch = mock.patch.object(config, "_client", lambda service: self.s3)
        patch.start()
        self.addCleanup(patch.stop)
        self.lib = library.Library()

    def test_a_key_songs_licenses_json_defines_is_accepted(self):
        self.assertEqual(self.lib.edit("a-one", {"license": "freedoom-bsd3"})["license"],
                         "freedoom-bsd3")

    def test_a_key_it_does_not_define_is_refused_with_the_choices(self):
        with self.assertRaises(ValueError) as cm:
            self.lib.edit("a-one", {"license": "BSD3"})
        self.assertIn("freedoom-bsd3", str(cm.exception))
        self.assertEqual(self.lib.get("a-one")["license"], "owner-supplied")   # unchanged

    def test_an_empty_licence_means_the_default_not_nothing(self):
        """The UI sends a cleared input as null; canon.py would then have no key to resolve."""
        self.assertEqual(self.lib.edit("a-one", {"license": None})["license"], "owner-supplied")

    def test_a_folder_can_be_relicensed_in_one_go(self):
        self.assertEqual(self.lib.bulk_edit(["a-one"], {"license": "cc0"}), {"edited": 1})
        self.assertEqual(self.lib.get("a-one")["license"], "cc0")

    def test_editing_an_entry_older_than_the_field(self):
        """library.json outlives its schema: an entry written before `license` existed must be
        editable, not a KeyError building the roll-back snapshot."""
        del self.lib.doc["entries"]["a-one"]["license"]
        self.assertEqual(self.lib.edit("a-one", {"composer": "X"})["license"], "owner-supplied")


if __name__ == "__main__":
    unittest.main()
