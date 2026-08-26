"""render/cloud/submit.py song selection (no AWS: only song_ids is exercised)."""
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

CLOUD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cloud")
submit = None    # bound by setUpModule


def setUpModule():
    """submit.py lives in render/cloud. Scoped to the module, not done at import time: a
    bare insert runs during DISCOVERY of the whole render/sfr suite and is never undone
    (#19 test_planner.ImportHygieneTests enforces this)."""
    global submit
    sys.path.insert(0, CLOUD)
    import submit as _submit
    submit = _submit


def tearDownModule():
    if CLOUD in sys.path:
        sys.path.remove(CLOUD)


class SongIdsTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        repo = pathlib.Path(self.td.name)
        (repo / "songs").mkdir()
        (repo / "songs" / "songs.json").write_text(
            json.dumps({"songs": [{"id": "a"}, {"id": "b"}, {"id": "ericsfavorites-c"}]}))
        self.patch = mock.patch.object(submit, "REPO", repo)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.td.cleanup()

    @staticmethod
    def args(all_=False, *song):
        return types.SimpleNamespace(all=all_, song=list(song))

    def test_known_ids_in_corpus_order(self):
        self.assertEqual(submit.song_ids(self.args(True)), ["a", "b", "ericsfavorites-c"])
        self.assertEqual(submit.song_ids(self.args(False, "ericsfavorites-c", "a")),
                         ["a", "ericsfavorites-c"])

    def test_unknown_id_refuses_instead_of_dropping_it(self):
        """`--song freedom-e1m1` (typo) used to be dropped silently: the run went ahead with the
        other songs, or died with the misleading "no songs selected" when it was the only one."""
        with self.assertRaises(SystemExit) as cm:
            submit.song_ids(self.args(False, "a", "freedom-e1m1"))
        self.assertIn("freedom-e1m1", str(cm.exception))
        self.assertNotIn("a,", str(cm.exception))
        with self.assertRaises(SystemExit):
            submit.song_ids(self.args(False, "nope"))


if __name__ == "__main__":
    unittest.main()
