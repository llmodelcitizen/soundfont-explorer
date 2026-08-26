"""Tests for the one path a MIDI takes: library.json -> fragment.py -> canon.py -> songs.json."""
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
         "license": "owner-supplied", "license_fields": None,
         "extra_include_classes": None, "inject_note": None,
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


LICENSES = {
    "owner-supplied": {"id": "owner-supplied", "url": None,
                       "notice_text": "Owner-supplied file imported from songs/import/."},
    "mutopia-pd": {"id": "PD", "url": "https://mutopia.example/legal",
                   "notice_text": "{title} — typeset by {sequencer} ({mutopia_id}), public domain."},
    "freedoom-bsd3": {"id": "BSD-3-Clause", "url": "https://freedoom.example/COPYING",
                      "notice_file": "LICENSES/freedoom-COPYING.adoc"},
}


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

    def test_it_carries_everything_canon_needs_to_publish_a_song(self):
        """The library is the only source of songs, so a field it drops here is a field the
        published songs.json loses: the Freedoom tracks would silently become owner-supplied
        and the piano pieces would stop rendering their piano-only variants."""
        lib = {"schema": 1, "entries": {"maple": lib_entry(
            "maple", "EricsFavorites/mutopia-maple.mid", "Maple Leaf Rag",
            composer="Scott Joplin", sequencer="Chris Sawer", license="mutopia-pd",
            license_fields={"mutopia_id": "Mutopia-2011/11/13-23"},
            extra_include_classes=["single_instrument:piano"],
            inject_note="re-voiced by pitch")}}
        spec = fragment.build_fragment(lib)["songs"][0]
        self.assertEqual(spec["license"], "mutopia-pd")
        self.assertEqual(spec["license_fields"], {"mutopia_id": "Mutopia-2011/11/13-23"})
        self.assertEqual(spec["extra_include_classes"], ["single_instrument:piano"])
        self.assertEqual(spec["inject_note"], "re-voiced by pitch")

    def test_an_entry_older_than_the_licence_field_is_owner_supplied(self):
        old = lib_entry("a", "a.mid", "a")
        del old["license"]
        spec = fragment.build_fragment({"entries": {"a": old}})["songs"][0]
        self.assertEqual(spec["license"], "owner-supplied")


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
        with open(os.path.join(self.tmp, "licenses.json"), "w") as fh:
            json.dump({"schema": 1, "licenses": LICENSES}, fh)
        os.makedirs(os.path.join(self.tmp, "LICENSES"))
        with open(os.path.join(self.tmp, "LICENSES", "freedoom-COPYING.adoc"), "w") as fh:
            fh.write("Redistributions of source code must retain the above copyright notice.\n")
        os.makedirs(os.path.join(self.tmp, "import/FILES/game"))
        with open(os.path.join(self.tmp, "import/FILES/game/good.mid"), "wb") as fh:
            fh.write(tiny_song(title="Good Tune"))
        with open(os.path.join(self.tmp, "import/FILES/game/bad.mid"), "wb") as fh:
            fh.write(tiny_song(program=False))

    def _restore(self):
        canon.SONGS_DIR = self._songs_dir
        canon.RENDERED_DIR = self._rendered

    def spec(self, sid, src, title, path=None, **kw):
        spec = {"id": sid, "src": src, "title": title, "license": "owner-supplied"}
        if path is not None:
            spec["path"] = path
        spec.update(kw)
        return spec

    def good(self, sid="game-good", title="Good Tune", **kw):
        return self.spec(sid, "import/FILES/game/good.mid", title, "game", **kw)

    def run_(self, specs, **kw):
        kw.setdefault("default_id", specs[0]["id"])
        return canon.run_public(specs, check=False, **kw)

    # ------------------------------------------------------------ the fragment is the corpus

    def test_the_fragment_is_the_only_source_of_songs(self):
        """canon.py reads corpus-imports.json and nothing else — there is no committed corpus
        to fall back on, so a tree without a fragment simply has no songs."""
        self.assertEqual(canon.load_specs(), [])
        with open(os.path.join(self.tmp, "corpus-imports.json"), "w") as fh:
            json.dump({"schema": 1, "songs": [self.good()]}, fh)
        self.assertEqual([s["id"] for s in canon.load_specs()], ["game-good"])

    def test_a_song_that_is_not_a_library_file_is_refused(self):
        with self.assertRaises(SystemExit):
            canon.resolve_import({"id": "x", "src": "src/hand-placed.mid"})

    def test_titles_are_leaf_names_and_a_refusal_is_data(self):
        entries, ok, refused, dropped = self.run_(
            [self.good(), self.spec("game-bad", "import/FILES/game/bad.mid", "bad", "game")],
            lenient=True)
        self.assertEqual([e["id"] for e in entries], ["game-good"])
        self.assertEqual(entries[0]["title"], "Good Tune")         # leaf, not game/Good Tune
        self.assertEqual(entries[0]["path"], "game")
        self.assertEqual(entries[0]["file"], "rendered/game/good.mid")
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0]["id"], "game-bad")
        self.assertIn("program change", refused[0]["reason"])
        doc = json.load(open(os.path.join(self.tmp, "songs.json")))
        self.assertEqual([e["id"] for e in doc["songs"]], ["game-good"])

    def test_the_pinned_id_wins_over_the_derived_one(self):
        """library.json pins an id at upload; renaming the file must never move the song, or
        every render under work/renders/<id>/ and every published /s/<id>/ is orphaned."""
        entries, *_ = self.run_([self.good(sid="freedoom-e1m1", title="Steel and Brass")])
        self.assertEqual(entries[0]["id"], "freedoom-e1m1")
        self.assertEqual(entries[0]["title"], "Steel and Brass")

    def test_the_default_track_is_flagged_and_named(self):
        entries, *_ = self.run_([self.good(sid="freedoom-e1m1"), self.good(sid="other")],
                                default_id="freedoom-e1m1")
        self.assertEqual([e["default"] for e in entries], [True, False])
        self.assertEqual(json.load(open(os.path.join(self.tmp, "songs.json")))["default"],
                         "freedoom-e1m1")

    def test_a_corpus_that_lost_its_default_track_is_a_hard_failure(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_([self.good()], default_id="freedoom-e1m1")
        self.assertIn("freedoom-e1m1", str(cm.exception))

    def test_an_empty_corpus_is_not(self):
        """A checkout with no fragment has no songs and no default; that is a tree without a
        library, not a corpus that lost its default track."""
        entries, ok, refused, dropped = canon.run_public([], check=False)
        self.assertEqual(entries, [])

    # ------------------------------------------------------------ licences

    def test_the_licence_notice_reaches_songs_json(self):
        """Freedoom's BSD-3 attribution is a redistribution obligation: it has to survive the
        trip from songs/licenses.json into every published entry, verbatim."""
        entries, *_ = self.run_([self.good(license="freedoom-bsd3")])
        self.assertEqual(entries[0]["license"]["id"], "BSD-3-Clause")
        self.assertEqual(entries[0]["license"]["url"], "https://freedoom.example/COPYING")
        self.assertIn("retain the above copyright notice", entries[0]["license"]["notice_text"])

    def test_license_fields_fill_the_notice_template(self):
        entries, *_ = self.run_([self.good(
            title="Maple Leaf Rag", sequencer="Chris Sawer", license="mutopia-pd",
            license_fields={"mutopia_id": "Mutopia-2011/11/13-23"})])
        self.assertEqual(entries[0]["license"]["notice_text"],
                         "Maple Leaf Rag — typeset by Chris Sawer (Mutopia-2011/11/13-23), "
                         "public domain.")

    def test_an_unknown_licence_refuses_the_song_rather_than_relicensing_it(self):
        entries, ok, refused, dropped = self.run_(
            [self.good(), self.good(sid="wrong", license="not-a-licence")], lenient=True)
        self.assertEqual([e["id"] for e in entries], ["game-good"])
        self.assertIn("unknown license", refused[0]["reason"])

    def test_a_notice_template_with_nothing_to_fill_it_refuses_too(self):
        entries, ok, refused, dropped = self.run_(
            [self.good(), self.good(sid="bare", license="mutopia-pd")], lenient=True)
        self.assertIn("license_fields", refused[0]["reason"])

    def test_extra_include_classes_add_to_the_default_set(self):
        entries, *_ = self.run_([self.good(extra_include_classes=["single_instrument:piano"])])
        self.assertEqual(entries[0]["include_classes"],
                         canon.DEFAULT_INCLUDE_CLASSES + ["single_instrument:piano"])

    def test_inject_note_is_appended_to_the_published_modifications(self):
        entries, *_ = self.run_([self.good(
            inject=[{"track": 1, "channel": 1, "program": 40, "name": "Violin"}],
            inject_note="re-voiced as a string band")])
        self.assertIn("re-voiced as a string band", entries[0]["modifications"])

    # ------------------------------------------------------------ targeted runs

    def test_only_merges_into_existing(self):
        self.run_([self.good()])
        # second import appears; --only must touch it alone and keep game-good untouched
        entries, ok, refused, dropped = self.run_(
            [self.good(), self.good(sid="game-good-2", title="Again")],
            only={"game-good-2"})
        self.assertEqual([e["id"] for e in entries], ["game-good", "game-good-2"])
        self.assertEqual(entries[1]["title"], "Again")
        doc = json.load(open(os.path.join(self.tmp, "songs.json")))
        self.assertEqual([e["id"] for e in doc["songs"]], ["game-good", "game-good-2"])

    def test_only_on_a_tree_that_has_never_run_canon_starts_a_songs_json(self):
        """songs.json is generated from the library, so a fresh box has none until the first
        run — a targeted run there is the first entry, not a crash."""
        entries, *_ = self.run_([self.good()], only={"game-good"})
        self.assertEqual([e["id"] for e in entries], ["game-good"])

    def test_only_drops_a_selected_song_that_is_refused(self):
        specs = [self.good(), self.good(sid="game-two", title="Two")]
        self.run_(specs)
        # the track's source goes bad (no program change) and only it is re-checked: its
        # old songs.json entry must go, not linger as a renderable song (#19)
        specs[1]["src"] = "import/FILES/game/bad.mid"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            entries, ok, refused, dropped = self.run_(specs, lenient=True, only={"game-two"})
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
        specs = [self.good(), self.good(sid="game-two", title="Two")]
        frag = os.path.join(self.tmp, "corpus-imports.json")
        canon.DEFAULT_SONG_ID, keep = "game-good", canon.DEFAULT_SONG_ID
        self.addCleanup(setattr, canon, "DEFAULT_SONG_ID", keep)
        with contextlib.redirect_stdout(io.StringIO()):
            with open(frag, "w") as fh:
                json.dump({"schema": 1, "songs": specs}, fh)
            canon.main(["--lenient"])
            specs[1]["src"] = "import/FILES/game/bad.mid"
            with open(frag, "w") as fh:
                json.dump({"schema": 1, "songs": specs}, fh)
            canon.main(["--lenient", "--only", "game-two"])
        with open(os.path.join(self.tmp, "canon-report.json")) as fh:
            report = json.load(fh)
        self.assertEqual(report["dropped"], ["game-two"])
        self.assertEqual([r["id"] for r in report["refused"]], ["game-two"])

    def test_only_is_quiet_when_it_drops_nothing(self):
        self.run_([self.good()])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.run_([self.good()], only={"game-good"})
        self.assertNotIn("dropped from songs.json", out.getvalue())


if __name__ == "__main__":
    unittest.main()
