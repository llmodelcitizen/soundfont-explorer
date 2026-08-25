"""The one header table every publisher uses (sfr.publish.OBJECT_KINDS) and the cloud shard's
uploads (#14)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sfr import publish

CLOUD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cloud")
OPUS, PK, JSON = "audio/ogg; codecs=opus", "application/octet-stream", "application/json"


def _import_shard():
    """render/cloud/shard.py reads its buckets from the environment at import."""
    sys.path.insert(0, CLOUD)
    try:
        with mock.patch.dict(os.environ, {"SFR_FONTS_BUCKET": "fonts", "SFR_SITE_BUCKET": "site"}):
            import shard
    finally:
        sys.path.remove(CLOUD)
    return shard


def _flag(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


class TestHeaderTable(unittest.TestCase):
    def test_headers_for_every_published_kind(self):
        self.assertEqual(publish.headers_for("a/song/l/0123abcd/000.opus"), (OPUS, publish.IMMUTABLE))
        self.assertEqual(publish.headers_for("a/song/g/0123abcd/0001.pk"), (PK, publish.IMMUTABLE))
        self.assertEqual(publish.headers_for("s/song/0123abcd.json"), (JSON, publish.IMMUTABLE))
        self.assertEqual(publish.headers_for("c/0123abcd.json"), (JSON, publish.IMMUTABLE))

    def test_no_headers_for_what_no_publisher_writes(self):
        for key in ("songs.json", "index.html", "assets/app.js", "a/song/g/h/0001.pk.tmp", "a/", "a",
                    "u/ulid/l/h/000.opus", "s/song/x.opus"):
            self.assertIsNone(publish.headers_for(key), key)

    def test_publish_commands_stamp_every_kind(self):
        cmds = publish.commands(Path("/pub"), "bkt", "DIST", dry_run=False)
        syncs = [c for c in cmds if c[:3] == ["aws", "s3", "sync"]]
        self.assertEqual(len(syncs), len(publish.OBJECT_KINDS))
        for (pre, glob, ctype), c in zip(publish.OBJECT_KINDS, syncs):
            self.assertEqual(c[3:5], [f"/pub/{pre}/", f"s3://bkt/{pre}/"])
            self.assertEqual(c[c.index("--exclude"):c.index("--exclude") + 4], ["--exclude", "*", "--include", glob])
            self.assertEqual(_flag(c, "--content-type"), ctype)
            self.assertEqual(_flag(c, "--cache-control"), publish.IMMUTABLE)
            self.assertIn("--size-only", c)
        cp, = [c for c in cmds if c[:3] == ["aws", "s3", "cp"]]
        self.assertEqual(cp[3:5], ["/pub/songs.json", "s3://bkt/songs.json"])
        self.assertEqual(_flag(cp, "--cache-control"), publish.SONGS_CC)
        self.assertEqual(cmds[-1][:3], ["aws", "cloudfront", "create-invalidation"])
        dry = publish.commands(Path("/pub"), "bkt", "DIST", dry_run=True)
        self.assertTrue(all("--dryrun" in c for c in dry))
        self.assertFalse(any(c[1] == "cloudfront" for c in dry))


class TestShardSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shard = _import_shard()

    def test_shares_the_publish_table(self):
        self.assertIs(self.shard.OBJECT_KINDS, publish.OBJECT_KINDS)
        self.assertIs(self.shard.IMMUTABLE, publish.IMMUTABLE)

    def test_sync_commands_stamp_headers_per_kind(self):
        with tempfile.TemporaryDirectory() as td:
            pub = Path(td)
            for pre in ("a", "c", "s"):
                (pub / pre).mkdir()
            cmds = self.shard.sync_commands(pub, "site")
        self.assertEqual(len(cmds), len(publish.OBJECT_KINDS))
        for (pre, glob, ctype), c in zip(publish.OBJECT_KINDS, cmds):
            self.assertEqual(c[:2], ["s5cmd", "sync"])
            self.assertIn("--size-only", c)
            self.assertEqual(_flag(c, "--include"), glob)
            self.assertEqual(_flag(c, "--content-type"), ctype)
            self.assertEqual(_flag(c, "--cache-control"), publish.IMMUTABLE)
            self.assertEqual(c[-2:], [f"{pub / pre}/", f"s3://site/{pre}/"])   # flags before the paths

    def test_sync_commands_skip_absent_prefixes(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "a").mkdir()
            cmds = self.shard.sync_commands(Path(td), "site")
        self.assertEqual([c[-1] for c in cmds], ["s3://site/a/", "s3://site/a/"])
        self.assertEqual({_flag(c, "--include") for c in cmds}, {"*.opus", "*.pk"})

    def test_publish_song_uploads_with_headers(self):
        shard = self.shard
        report = json.dumps({"songs": {"x": {"variants": 3}}})
        calls = []
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "public" / "a").mkdir(parents=True)
            with mock.patch.object(shard, "OUT", Path(td)), \
                 mock.patch.object(shard, "sh", side_effect=lambda argv, **kw: calls.append(argv)), \
                 mock.patch.object(shard.subprocess, "run",
                                   return_value=subprocess.CompletedProcess([], 0, stdout="report\n" + report, stderr="")), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(shard.publish_song("x"))
        self.assertEqual([c[:2] for c in calls], [["s5cmd", "sync"]] * 2)
        for c in calls:
            self.assertEqual(_flag(c, "--cache-control"), publish.IMMUTABLE)
            self.assertIn(_flag(c, "--content-type"), (OPUS, PK))


if __name__ == "__main__":
    unittest.main()
