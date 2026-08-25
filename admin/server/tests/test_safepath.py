"""SPA catch-all containment (issue #3). Stdlib only — runs in CI without FastAPI."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sfadmin.safepath import contained_file  # noqa: E402


class ContainedFileTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        top = self.td.name
        self.root = os.path.join(top, "dist")
        os.makedirs(os.path.join(self.root, "assets"))
        with open(os.path.join(self.root, "index.html"), "w") as f:
            f.write("shell")
        with open(os.path.join(self.root, "assets", "app.js"), "w") as f:
            f.write("js")
        self.secret = os.path.join(top, "secret.txt")
        with open(self.secret, "w") as f:
            f.write("TOPSECRET")
        os.symlink(self.secret, os.path.join(self.root, "link-out"))
        os.symlink(os.path.join(self.root, "index.html"), os.path.join(self.root, "link-in"))

    def tearDown(self):
        self.td.cleanup()

    def test_files_inside_root(self):
        self.assertEqual(contained_file(self.root, "index.html"), os.path.realpath(os.path.join(self.root, "index.html")))
        self.assertEqual(contained_file(self.root, "assets/app.js"), os.path.realpath(os.path.join(self.root, "assets", "app.js")))
        self.assertEqual(contained_file(self.root, "assets/../index.html"), os.path.realpath(os.path.join(self.root, "index.html")))
        self.assertEqual(contained_file(self.root, "link-in"), os.path.realpath(os.path.join(self.root, "index.html")))
        # root given with a trailing slash or via a symlink still works
        self.assertIsNotNone(contained_file(self.root + "/", "index.html"))

    def test_escapes_are_refused(self):
        for rel in ("../secret.txt", "../../secret.txt", "assets/../../secret.txt",
                    "/" + self.secret.lstrip("/"), "//" + self.secret.lstrip("/"),
                    "/etc/passwd", "//etc/passwd", "../../../../../../etc/passwd",
                    "link-out", "assets/../link-out"):
            self.assertIsNone(contained_file(self.root, rel), rel)

    def test_non_files_are_none(self):
        for rel in ("", "assets", "assets/", ".", "missing.html", "index.html\x00.txt"):
            self.assertIsNone(contained_file(self.root, rel), repr(rel))


if __name__ == "__main__":
    unittest.main()
