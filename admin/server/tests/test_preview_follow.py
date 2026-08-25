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


class OpenStreamTests(unittest.TestCase):
    """open_stream(): the route must learn about a failed render before it commits to 200."""

    def test_failed_before_output_raises_with_the_reason(self):
        job = preview.Job(SHA)
        job.error = "render failed (fluidsynth=1, ffmpeg=0)"
        job.done.set()
        with self.assertRaises(RuntimeError) as cm:
            preview.open_stream(job)
        self.assertIn("fluidsynth=1", str(cm.exception))

    def test_failed_after_opening_an_empty_file_raises(self):
        job = preview.Job(SHA)

        def writer():
            open(job.tmp, "wb").close()          # got a slot, then the pipeline died
            time.sleep(0.2)
            job.error = "boom"
            os.remove(job.tmp)
            job.done.set()

        t = threading.Thread(target=writer)
        t.start()
        with self.assertRaises(RuntimeError) as cm:
            preview.open_stream(job)
        t.join()
        self.assertEqual(str(cm.exception), "boom")

    def test_success_streams_everything(self):
        job = preview.Job(SHA)

        def writer():
            with open(job.tmp, "wb") as fh:
                for part in (b"aa", b"bb"):
                    fh.write(part)
                    fh.flush()
                    time.sleep(0.05)
            os.replace(job.tmp, preview.cache_path(SHA))
            job.done.set()

        t = threading.Thread(target=writer)
        t.start()
        stream = preview.open_stream(job)      # returns once the first bytes exist
        self.assertEqual(b"".join(stream), b"aabb")
        t.join()
        os.remove(preview.cache_path(SHA))

    def test_a_queued_render_does_not_hold_the_route(self):
        """preview_mp3 is a sync route, so this call sits on an anyio threadpool worker and
        no client disconnect can break it out. Priming with next(follow(job)) blocked there
        for as long as the render took (up to RENDER_TIMEOUT_S) — a burst of clicks pinned
        the pool (#19). Past the prime window the response starts and follow() tails it."""
        job = preview.Job(SHA)                   # queued for a render slot: no file at all
        t0 = time.monotonic()
        stream = preview.open_stream(job, prime_s=0.3)
        self.assertLess(time.monotonic() - t0, 3)
        with open(job.tmp, "wb") as fh:          # the render gets its slot after the headers
            fh.write(b"late")
        os.replace(job.tmp, preview.cache_path(SHA))
        job.done.set()
        self.assertEqual(b"".join(stream), b"late")
        os.remove(preview.cache_path(SHA))

    def test_a_failure_inside_the_prime_window_still_raises(self):
        job = preview.Job(SHA)

        def writer():
            time.sleep(0.2)
            job.error = "render failed (fluidsynth=1, ffmpeg=0)"
            job.done.set()

        t = threading.Thread(target=writer)
        t.start()
        with self.assertRaises(RuntimeError) as cm:
            preview.open_stream(job, prime_s=5)
        t.join()
        self.assertIn("fluidsynth=1", str(cm.exception))

    def test_close_reaches_follow(self):
        job = preview.Job(SHA)
        with open(job.tmp, "wb") as fh:
            fh.write(b"x" * (64 * 1024 + 1))
        stream = preview.open_stream(job)
        stream.close()                          # client went away mid-stream
        os.replace(job.tmp, preview.cache_path(SHA))
        job.done.set()
        self.assertEqual(b"".join(stream), b"")  # closed generators yield nothing more
        os.remove(preview.cache_path(SHA))


if __name__ == "__main__":
    unittest.main()
