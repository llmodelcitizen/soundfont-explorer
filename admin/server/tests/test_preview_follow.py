"""preview.follow(): tail a growing render file without holding a render slot."""
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("SFADMIN_BUCKET", "test")
os.environ.setdefault("SFADMIN_HOSTNAME", "test")
os.environ["SFADMIN_CACHE"] = tempfile.mkdtemp()
from sfadmin import preview  # noqa: E402

SHA = "0" * 64


class FollowTests(unittest.TestCase):
    def collect(self, job):
        return b"".join(preview.follow(job))

    def test_tails_growing_file_through_rename(self):
        job = preview.Job(SHA)

        def writer():
            with open(job.tmp, "wb") as fh:
                for part in (b"aa", b"bb", b"cc"):
                    fh.write(part)
                    fh.flush()
                    time.sleep(0.05)
            os.replace(job.tmp, preview.cache_path(SHA))  # rename keeps the reader's inode
            job.done.set()

        t = threading.Thread(target=writer)
        t.start()
        got = self.collect(job)
        t.join()
        self.assertEqual(got, b"aabbcc")
        os.remove(preview.cache_path(SHA))

    def test_failed_job_yields_nothing(self):
        job = preview.Job(SHA)
        job.error = "boom"
        job.done.set()  # failed before creating the file
        self.assertEqual(self.collect(job), b"")

    def test_queued_then_written(self):
        job = preview.Job(SHA)

        def writer():
            time.sleep(0.3)  # simulates waiting for a render slot: no file yet
            with open(job.tmp, "wb") as fh:
                fh.write(b"late")
            os.replace(job.tmp, preview.cache_path(SHA))
            job.done.set()

        t = threading.Thread(target=writer)
        t.start()
        self.assertEqual(self.collect(job), b"late")
        t.join()
        os.remove(preview.cache_path(SHA))


if __name__ == "__main__":
    unittest.main()
