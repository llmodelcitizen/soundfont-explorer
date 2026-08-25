"""Tests for the admin-library bridge: fragment.py and canon.py's fragment/lenient/--only paths."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import smf as S            # noqa: E402
import canon               # noqa: E402
import fragment            # noqa: E402


def lib_entry(sid, path, name, **kw):
    e = {"id": sid, "path": path, "name": name, "sha256": "0" * 64, "size": 1,
         "composer": None, "sequencer": None, "source_url": None, "hidden": False,
         "inject": None, "trim": None, "notes": None,
         "added_at": "2026-08-23T00:00:00Z", "modified_at": "2026-08-23T00:00:00Z",
         "canon": {"status": "pending", "reason": None, "canonical_sha256": None,
                   "duration_s": None, "checked_at": None}}
    e.update(kw)
    return e


def meta(tick, mtype, data):
    return S.Event(tick, "meta", 0xFF, data, mtype)


def ch(tick, status, *data):
    return S.Event(tick, "channel", status, bytes(data))


def tiny_song(title=None, program=True):
    """One channel-0 melody note; with program=False check_programs must refuse it."""
    t0 = [meta(0, 0x51, (500000).to_bytes(3, "big"))]
    if title:
        t0.append(meta(0, 0x03, title.encode()))
    t1 = []
    if program:
        t1.append(ch(0, 0xC0, 33))
    t1 += [ch(0, 0x90, 60, 100), ch(480, 0x80, 60, 0)]
    return S.serialize(S.Smf(1, 480, [t0, t1]))


class FragmentTests(unittest.TestCase):
    def test_build_fragment(self):
        lib = {"schema": 1, "entries": {
            "b-two": lib_entry("b-two", "b/two.mid", "two", composer="X",
                               inject=[{"track": 1, "channel": 1, "program": 0}]),
            "a-one": lib_entry("a-one", "a/one.mid", "one"),
            "root": lib_entry("root", "root.mid", "root"),
            "gone": lib_entry("gone", "a/gone.mid", "gone", hidden=True),
        }}
        frag = fragment.build_fragment(lib)
        ids = [s["id"] for s in frag["songs"]]
        self.assertEqual(ids, ["a-one", "b-two", "root"])          # sorted by path, hidden gone
        by_id = {s["id"]: s for s in frag["songs"]}
        self.assertEqual(by_id["a-one"]["path"], "a")
        self.assertEqual(by_id["a-one"]["src"], "import/FILES/a/one.mid")
        self.assertEqual(by_id["a-one"]["license"], "owner-supplied")
        self.assertNotIn("path", by_id["root"])                    # root: no dir, no path key
        self.assertNotIn("composer", by_id["a-one"])               # nulls omitted
        self.assertEqual(by_id["b-two"]["composer"], "X")
        self.assertEqual(by_id["b-two"]["inject"], [{"track": 1, "channel": 1, "program": 0}])


class CanonBridgeTests(unittest.TestCase):
    """Run canon against a scratch SONGS_DIR so nothing touches the real tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self._songs_dir = canon.SONGS_DIR
        self._rendered = canon.RENDERED_DIR
        canon.SONGS_DIR = self.tmp
        canon.RENDERED_DIR = os.path.join(self.tmp, "rendered")
        self.addCleanup(self._restore)
        os.makedirs(os.path.join(self.tmp, "import/FILES/game"))
        with open(os.path.join(self.tmp, "import/FILES/game/good.mid"), "wb") as fh:
            fh.write(tiny_song(title="Good Tune"))
        with open(os.path.join(self.tmp, "import/FILES/game/bad.mid"), "wb") as fh:
            fh.write(tiny_song(program=False))
        self.corpus = {
            "schema": 1, "tail_s": 1.0, "default": "game-good",
            "licenses": {"owner-supplied": {
                "id": "owner-supplied", "url": None,
                "notice_text": "Owner-supplied file imported from songs/import/."}},
            "songs": [
                {"id": "old-import", "src": "import/FILES/game/good.mid", "license": "owner-supplied"},
            ],
        }

    def _restore(self):
        canon.SONGS_DIR = self._songs_dir
        canon.RENDERED_DIR = self._rendered

    def frag_spec(self, sid, src, title, path=None, **kw):
        spec = {"id": sid, "src": src, "title": title, "license": "owner-supplied"}
        if path is not None:
            spec["path"] = path
        spec.update(kw)
        return spec

    def test_merge_fragment_supersedes_imports(self):
        with open(os.path.join(self.tmp, "corpus-imports.json"), "w") as fh:
            json.dump({"schema": 1, "songs": [
                self.frag_spec("game-good", "import/FILES/game/good.mid", "Good Tune", "game"),
            ]}, fh)
        corpus = dict(self.corpus)
        corpus["songs"] = corpus["songs"] + [{"id": "curated", "generator": "x"}]
        merged = canon.merge_fragment(corpus)
        ids = [s.get("id") for s in merged["songs"]]
        self.assertEqual(ids, ["curated", "game-good"])            # import superseded, curated kept

    def test_merge_fragment_absent_is_identity(self):
        self.assertIs(canon.merge_fragment(self.corpus), self.corpus)

    def test_fragment_titles_and_lenient(self):
        self.corpus["songs"] = [
            self.frag_spec("game-good", "import/FILES/game/good.mid", "Good Tune", "game"),
            self.frag_spec("game-bad", "import/FILES/game/bad.mid", "bad", "game"),
        ]
        entries, ok, refused, dropped = canon.run_public(self.corpus, check=False, lenient=True)
        self.assertEqual([e["id"] for e in entries], ["game-good"])
        self.assertEqual(entries[0]["title"], "Good Tune")         # leaf, not game/Good Tune
        self.assertEqual(entries[0]["path"], "game")
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["id"], "game-bad")
        self.assertIn("program change", refused[0]["reason"])
        doc = json.load(open(os.path.join(self.tmp, "songs.json")))
        self.assertEqual([e["id"] for e in doc["songs"]], ["game-good"])

    def test_legacy_import_title_keeps_prefix(self):
        self.corpus["default"] = "old-import"
        entries, ok, refused, dropped = canon.run_public(self.corpus, check=False)
        self.assertEqual(entries[0]["id"], "old-import")           # explicit id honored
        self.assertEqual(entries[0]["title"], "game/Good Tune")    # old behavior: prefixed
        self.assertNotIn("path", entries[0])

    def test_only_merges_into_existing(self):
        self.corpus["songs"] = [
            self.frag_spec("game-good", "import/FILES/game/good.mid", "Good Tune", "game"),
        ]
        canon.run_public(self.corpus, check=False)
        # second import appears; --only must touch it alone and keep game-good untouched
        self.corpus["songs"].append(
            self.frag_spec("game-good-2", "import/FILES/game/good.mid", "Again", "game"))
        entries, ok, refused, dropped = canon.run_public(self.corpus, check=False, only={"game-good-2"})
        self.assertEqual([e["id"] for e in entries], ["game-good", "game-good-2"])
        self.assertEqual(entries[1]["title"], "Again")
        doc = json.load(open(os.path.join(self.tmp, "songs.json")))
        self.assertEqual([e["id"] for e in doc["songs"]], ["game-good", "game-good-2"])

    def test_only_drops_a_selected_song_that_is_refused(self):
        self.corpus["songs"] = [
            self.frag_spec("game-good", "import/FILES/game/good.mid", "Good Tune", "game"),
            self.frag_spec("game-two", "import/FILES/game/good.mid", "Two", "game"),
        ]
        canon.run_public(self.corpus, check=False)
        # the track's source goes bad (no program change) and only it is re-checked: its
        # old songs.json entry must go, not linger as a renderable song (#19)
        self.corpus["songs"][1]["src"] = "import/FILES/game/bad.mid"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            entries, ok, refused, dropped = canon.run_public(self.corpus, check=False, lenient=True,
                                                    only={"game-two"})
        self.assertEqual([r["id"] for r in refused], ["game-two"])
        self.assertEqual([e["id"] for e in entries], ["game-good"])
        doc = json.load(open(os.path.join(self.tmp, "songs.json")))
        self.assertEqual([e["id"] for e in doc["songs"]], ["game-good"])
        # dropping it is right, but it is also the moment the admin's publish path starts
        # refusing the track while it is still live on the site — the run has to say so (#19)
        self.assertIn("game-two", out.getvalue())
        self.assertIn("dropped from songs.json", out.getvalue())
        self.assertIn("live on the site", out.getvalue())
        # ... and say it where the admin UI can read it: canon.py's stdout is inherited by
        # library.canon_run on purpose, so the print above only reaches the journal (#19)
        self.assertEqual(dropped, ["game-two"])

    def test_the_report_carries_the_dropped_ids(self):
        self.corpus["songs"] = [
            self.frag_spec("game-good", "import/FILES/game/good.mid", "Good Tune", "game"),
            self.frag_spec("game-two", "import/FILES/game/good.mid", "Two", "game"),
        ]
        with open(os.path.join(self.tmp, "corpus.json"), "w") as fh:
            json.dump(self.corpus, fh)
        with contextlib.redirect_stdout(io.StringIO()):
            canon.main(["--lenient"])
            self.corpus["songs"][1]["src"] = "import/FILES/game/bad.mid"
            with open(os.path.join(self.tmp, "corpus.json"), "w") as fh:
                json.dump(self.corpus, fh)
            canon.main(["--lenient", "--only", "game-two"])
        with open(os.path.join(self.tmp, "canon-report.json")) as fh:
            report = json.load(fh)
        self.assertEqual(report["dropped"], ["game-two"])
        self.assertEqual([r["id"] for r in report["refused"]], ["game-two"])

    def test_only_is_quiet_when_it_drops_nothing(self):
        self.corpus["songs"] = [
            self.frag_spec("game-good", "import/FILES/game/good.mid", "Good Tune", "game"),
        ]
        canon.run_public(self.corpus, check=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            canon.run_public(self.corpus, check=False, only={"game-good"})
        self.assertNotIn("dropped from songs.json", out.getvalue())


if __name__ == "__main__":
    unittest.main()
