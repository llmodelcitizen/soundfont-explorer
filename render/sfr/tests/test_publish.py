"""The one header table every publisher uses (sfr.publish.OBJECT_KINDS), the cloud shard's
uploads, and `sfr publish --restamp` (#14)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from sfr import publish
from sfr.config import Paths

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
            # the song's own subtree, plus a neighbour that must NOT be re-walked (#25)
            (Path(td) / "public" / "a" / "x").mkdir(parents=True)
            (Path(td) / "public" / "a" / "other").mkdir(parents=True)
            with mock.patch.object(shard, "OUT", Path(td)), \
                 mock.patch.object(shard, "sh", side_effect=lambda argv, **kw: calls.append(argv)), \
                 mock.patch.object(shard.subprocess, "run",
                                   return_value=subprocess.CompletedProcess([], 0, stdout="report\n" + report, stderr="")), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(shard.publish_song("x"))
        # s5cmd, then tuning flags (#25 sets --numworkers), then the sync verb
        self.assertEqual(len(calls), 2)
        for c in calls:
            self.assertEqual(c[0], "s5cmd")
            self.assertIn("sync", c)
            self.assertEqual(_flag(c, "--cache-control"), publish.IMMUTABLE)
            self.assertTrue(c[-1].startswith("s3://site/a/x/"), c)
            self.assertNotIn("other", c[-2])
            self.assertIn(_flag(c, "--content-type"), (OPUS, PK))


# ---------------------------------------------------------------- restamp

def obj(ctype, cc, modified):
    d = {"LastModified": modified, "ContentType": ctype}
    if cc:
        d["CacheControl"] = cc
    return d


HOST, SHARD, LATER = "2026-08-21T10:31:48+00:00", "2026-08-23T01:00:00+00:00", "2026-08-24T03:00:00+00:00"
BUCKET = {
    # host-published: right everywhere
    "a/good/g/h1/0000.pk": obj(PK, publish.IMMUTABLE, HOST),
    "a/good/l/h2/000.opus": obj(OPUS, publish.IMMUTABLE, HOST),
    "s/good/aaaa.json": obj(JSON, publish.IMMUTABLE, HOST),
    # shard-published before #14: mime.types guesses, no Cache-Control
    "a/bad/g/h3/0000.pk": obj("application/x-tex-pk", None, SHARD),
    "a/bad/g/h3/0001.pk": obj("application/x-tex-pk", None, SHARD),
    "a/bad/l/h4/000.opus": obj("audio/ogg", None, SHARD),
    "s/bad/bbbb.json": obj(JSON, None, SHARD),
    # host-published, then a later shard batch added packs only
    "a/mixed/g/h5/0000.pk": obj(PK, publish.IMMUTABLE, HOST),
    "a/mixed/g/h6/0000.pk": obj("application/x-tex-pk", None, LATER),
    "a/mixed/l/h7/000.opus": obj(OPUS, publish.IMMUTABLE, HOST),
    "s/mixed/cccc.json": obj(JSON, publish.IMMUTABLE, HOST),
    "c/dddd.json": obj(JSON, publish.IMMUTABLE, HOST),
    "songs.json": obj(JSON, publish.SONGS_CC, LATER),
    "index.html": obj("text/html", publish.SONGS_CC, LATER),
}


class FakeAws:
    """subprocess.run stand-in answering the aws CLI calls restamp() makes from BUCKET and
    recording everything that would write."""

    def __init__(self, objects):
        self.objects, self.writes, self.heads = objects, [], []
        self.lock = threading.Lock()

    def __call__(self, argv, **kw):
        out = ""
        opts = dict(zip(argv[3::2], argv[4::2]))
        if argv[:3] == ["aws", "s3api", "list-objects-v2"]:
            prefix = opts["--prefix"]
            keys = sorted(k for k in self.objects if k.startswith(prefix))
            if "--delimiter" in opts:
                out = json.dumps(sorted({prefix + k[len(prefix):].split("/")[0] + "/" for k in keys
                                         if "/" in k[len(prefix):]}) or None)
            else:
                out = json.dumps([[k, self.objects[k]["LastModified"]] for k in keys] or None)
        elif argv[:3] == ["aws", "s3api", "head-object"]:
            with self.lock:
                self.heads.append(opts["--key"])
            out = json.dumps(self.objects[opts["--key"]])
        else:
            with self.lock:
                self.writes.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")


class TestRestamp(unittest.TestCase):
    def run_restamp(self, dry_run, distribution="DIST"):
        aws, lines = FakeAws(BUCKET), []
        with tempfile.TemporaryDirectory() as td, mock.patch.object(publish.subprocess, "run", aws):
            rc = publish.restamp(Paths(out=Path(td) / "out"), "bkt", distribution, dry_run=dry_run,
                                 echo=lines.append, workers=3)
        return rc, aws, "\n".join(lines)

    def test_sample_keys_one_per_kind_and_upload_hour(self):
        listing = [(k, v["LastModified"]) for k, v in BUCKET.items() if k.startswith("a/mixed/")]
        self.assertEqual(sorted(publish.sample_keys(listing)),
                         ["a/mixed/g/h5/0000.pk", "a/mixed/g/h6/0000.pk", "a/mixed/l/h7/000.opus"])

    def test_stale(self):
        self.assertIsNone(publish.stale(BUCKET["a/good/g/h1/0000.pk"], "a/good/g/h1/0000.pk"))
        self.assertEqual(publish.stale(BUCKET["a/bad/g/h3/0000.pk"], "a/bad/g/h3/0000.pk"), "application/x-tex-pk / -")
        self.assertEqual(publish.stale({}, "a/x/l/h/000.opus"), "- / -")
        self.assertIsNone(publish.stale({}, "index.html"))        # not ours to stamp

    def test_dry_run_lists_and_writes_nothing(self):
        rc, aws, out = self.run_restamp(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertEqual(aws.writes, [])
        self.assertNotIn("songs.json", aws.heads)                  # only a/ s/ c/ are inspected
        self.assertLess(len(aws.heads), len(BUCKET))               # one HEAD per batch, not per object
        self.assertIn("7 prefixes, 12 objects; 3 prefixes (7 objects", out)   # a/bad 3 + a/mixed 3 + s/bad 1
        for scope in ("a/bad/", "s/bad/", "a/mixed/"):
            self.assertRegex(out, rf"(?m)^  {scope}\s", msg=scope)
        for scope in ("a/good/", "s/good/", "s/mixed/", "c/"):
            self.assertNotRegex(out, rf"(?m)^  {scope}\s", msg=scope)
        self.assertIn("*.pk: application/x-tex-pk / -", out)
        self.assertIn("*.opus: audio/ogg / -", out)
        self.assertIn("*.json: application/json / -", out)
        self.assertIn("$ aws s3 cp s3://bkt/a/bad/ s3://bkt/a/bad/ --recursive", out)
        self.assertIn("$ aws cloudfront create-invalidation --distribution-id DIST --paths /a/* /s/*", out)
        self.assertTrue(out.endswith("nothing changed"))

    def test_restamps_only_the_kinds_found_wrong_then_invalidates(self):
        rc, aws, out = self.run_restamp(dry_run=False)
        self.assertEqual(rc, 0)
        cps = [w for w in aws.writes if w[:3] == ["aws", "s3", "cp"]]
        self.assertEqual([(w[3], _flag(w, "--include")) for w in cps],
                         [("s3://bkt/a/bad/", "*.opus"), ("s3://bkt/a/bad/", "*.pk"),
                          ("s3://bkt/a/mixed/", "*.pk"), ("s3://bkt/s/bad/", "*.json")])
        for w in cps:
            self.assertEqual(w[3], w[4])                            # same key: a metadata rewrite
            self.assertEqual(_flag(w, "--metadata-directive"), "REPLACE")
            self.assertEqual(_flag(w, "--content-type"), publish.headers_for(w[3][len("s3://bkt/"):] + "x" + _flag(w, "--include")[1:])[0])
            self.assertEqual(_flag(w, "--cache-control"), publish.IMMUTABLE)
            self.assertIn("--recursive", w)
        self.assertEqual(aws.writes[-1], ["aws", "cloudfront", "create-invalidation", "--distribution-id", "DIST",
                                          "--paths", "/a/*", "/s/*"])
        self.assertNotIn("nothing changed", out)

    def test_no_distribution_no_invalidation(self):
        rc, aws, _ = self.run_restamp(dry_run=False, distribution=None)
        self.assertEqual(rc, 0)
        self.assertFalse(any(w[1] == "cloudfront" for w in aws.writes))

    def test_nothing_wrong_nothing_done(self):
        clean = {k: v for k, v in BUCKET.items() if publish.stale(v, k) is None}
        aws, lines = FakeAws(clean), []
        with tempfile.TemporaryDirectory() as td, mock.patch.object(publish.subprocess, "run", aws):
            rc = publish.restamp(Paths(out=Path(td) / "out"), "bkt", "DIST", dry_run=False, echo=lines.append)
        self.assertEqual(rc, 0)
        self.assertEqual(aws.writes, [])
        self.assertIn("0 prefixes (0 objects", lines[0])


if __name__ == "__main__":
    unittest.main()
