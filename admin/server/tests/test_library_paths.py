"""clean_rel_path: the only defense between client-supplied paths and the filesystem/S3."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("SFADMIN_BUCKET", "test")
os.environ.setdefault("SFADMIN_HOSTNAME", "test")
from sfadmin.library import clean_rel_path  # noqa: E402


class CleanRelPathTests(unittest.TestCase):
    def test_normalizes(self):
        self.assertEqual(clean_rel_path("/a/b/x.mid"), "a/b/x.mid")
        self.assertEqual(clean_rel_path("a//b/./x.MID"), "a/b/x.MID")
        self.assertEqual(clean_rel_path("x.rmi"), "x.rmi")
        self.assertEqual(clean_rel_path("dir with spaces/née #7.midi"), "dir with spaces/née #7.midi")
        self.assertEqual(clean_rel_path("a\\b\\x.mid"), "a/b/x.mid")

    def test_rejects_traversal_and_junk(self):
        for bad in ("../x.mid", "a/../x.mid", "a/..", "", "/", "a/b/", "x.mp3", "x.mid\0y",
                    "a/x.nsf", "x"):
            with self.assertRaises(ValueError, msg=bad):
                clean_rel_path(bad)


if __name__ == "__main__":
    unittest.main()
