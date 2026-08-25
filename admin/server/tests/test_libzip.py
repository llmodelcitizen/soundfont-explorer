"""libzip.ensure_zip(): one valid zip per library version, whatever the request concurrency."""
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sfadmin import libzip  # noqa: E402


class EnsureZipTests(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.src = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cache)
        self.addCleanup(shutil.rmtree, self.src)
        self.files = []
        for i in range(40):  # enough bytes that concurrent writers would interleave
            p = os.path.join(self.src, f"t{i}.mid")
            with open(p, "wb") as fh:
                fh.write(os.urandom(20_000))
            self.files.append((p, f"FILES/dir/t{i}.mid"))
        self.files.append((os.path.join(self.src, "missing.mid"), "FILES/missing.mid"))

    def check(self, path):
        with zipfile.ZipFile(path) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(sorted(z.namelist()), sorted(a for _, a in self.files[:-1]))

    def test_builds_once_and_serves_a_valid_zip(self):
        out = libzip.ensure_zip(self.cache, "2026-08-24T00:00:00Z", self.files)
        self.assertEqual(out, libzip.zip_path(self.cache, "2026-08-24T00:00:00Z"))
        self.check(out)
        self.assertEqual(sorted(os.listdir(self.cache)), [os.path.basename(out)])  # no temp left
        ino = os.stat(out).st_ino
        self.assertEqual(libzip.ensure_zip(self.cache, "2026-08-24T00:00:00Z", []), out)
        self.assertEqual(os.stat(out).st_ino, ino)   # served, not rebuilt (a build replaces)

    def test_concurrent_first_requests_share_one_valid_build(self):
        n = 8
        gate = threading.Barrier(n)
        results, errors = [], []

        def worker():
            try:
                gate.wait()
                results.append(libzip.ensure_zip(self.cache, "v1", self.files))
            except Exception as e:  # pragma: no cover - the assertion below reports it
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)
        self.check(results[0])
        self.assertEqual(sorted(os.listdir(self.cache)), [os.path.basename(results[0])])

    def test_new_version_replaces_stale_zips(self):
        old = libzip.ensure_zip(self.cache, "v1", self.files)
        os.utime(old, (0, 0))  # nobody has asked for this version in a long time
        with open(os.path.join(self.cache, "library-deadbeef.zip"), "wb") as fh:
            fh.write(b"stale")
        os.utime(os.path.join(self.cache, "library-deadbeef.zip"), (0, 0))
        with open(os.path.join(self.cache, "other.zip"), "wb") as fh:
            fh.write(b"not ours")
        new = libzip.ensure_zip(self.cache, "v2", self.files)
        self.assertNotEqual(old, new)
        self.check(new)
        self.assertEqual(sorted(os.listdir(self.cache)), sorted([os.path.basename(new), "other.zip"]))

    def test_a_version_just_handed_out_survives_the_next_build(self):
        """ensure_zip returns a path the route opens a statement later (FileResponse stats
        it at send time). A library edit in between used to delete it under that request —
        FileNotFoundError, i.e. one of the two 500s this module removed (#19)."""
        v1 = libzip.ensure_zip(self.cache, "v1", self.files)
        libzip.ensure_zip(self.cache, "v2", self.files)     # library edited between the two
        self.check(v1)                                      # still openable by that request
        os.utime(v1, (0, 0))                                # ... until nobody wants it
        libzip.ensure_zip(self.cache, "v3", self.files)
        self.assertFalse(os.path.exists(v1))

    def test_a_hand_out_sweeps_the_previous_version(self):
        """The sweep only ran on a build, so the version before the current one survived
        until the library next changed — indefinitely if it stopped changing. The old code
        kept exactly one whole-library zip in the cache dir; keep that ceiling (#19)."""
        v1 = libzip.ensure_zip(self.cache, "v1", self.files)
        v2 = libzip.ensure_zip(self.cache, "v2", self.files)
        os.utime(v1, (0, 0))                     # nobody has asked for v1 in a long time
        self.assertTrue(os.path.exists(v1))      # ... but no build has happened since
        self.assertEqual(libzip.ensure_zip(self.cache, "v2", []), v2)   # a plain hand-out
        self.assertFalse(os.path.exists(v1))
        self.check(v2)

    def test_a_temp_left_by_a_killed_build_is_swept(self):
        """The in-process finally covers a normal failure, but a process killed mid-build
        (an sfadmin restart) leaves `<out>.<pid>-<tid>.tmp` behind, and the sweep only
        looked at library-*.zip — so those accumulated in the cache dir for ever (#19)."""
        orphan = libzip.zip_path(self.cache, "v0") + ".999-1234.tmp"
        with open(orphan, "wb") as fh:
            fh.write(b"half a zip")
        fresh = libzip.zip_path(self.cache, "v0") + ".999-5678.tmp"   # another box, building now
        with open(fresh, "wb") as fh:
            fh.write(b"half a zip")
        os.utime(orphan, (0, 0))
        out = libzip.ensure_zip(self.cache, "v1", self.files)
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(os.path.exists(fresh))    # too young to be anything but a live build
        self.check(out)

    def test_failed_build_leaves_no_temp_and_no_zip(self):
        def files():
            yield self.files[0]
            raise OSError("mirror vanished mid-build")

        with self.assertRaises(OSError):
            libzip.ensure_zip(self.cache, "v3", files())
        self.assertEqual(os.listdir(self.cache), [])
        self.check(libzip.ensure_zip(self.cache, "v3", self.files))  # and the lock was released


if __name__ == "__main__":
    unittest.main()
