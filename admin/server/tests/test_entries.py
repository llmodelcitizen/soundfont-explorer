"""The shared library.json entry pieces: one `canon` block constructor, one MIDI_EXTS.

The literals these replaced were written out six times (library.py ×4, ingest.py ×1 plus the
edit-reset); library.json is diffed by eye, so both the key set and the key order are part of
the file format and are pinned here.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sfadmin.entries import CANON_KEYS, MIDI_EXTS, canon_state  # noqa: E402


class CanonStateTests(unittest.TestCase):
    def test_pending_matches_the_literal_it_replaced(self):
        self.assertEqual(canon_state("pending"),
                         {"status": "pending", "reason": None, "canonical_sha256": None,
                          "duration_s": None, "checked_at": None})

    def test_every_call_shape_keeps_the_key_order(self):
        for state in (canon_state("pending"),
                      canon_state("unparsed", "SmfError: bad header"),
                      canon_state("refused", "no programs", checked_at="2026-08-25T00:00:00Z"),
                      canon_state("ok", canonical_sha256="ab" * 32, duration_s=12.5,
                                  checked_at="2026-08-25T00:00:00Z")):
            self.assertEqual(tuple(state.keys()), CANON_KEYS)

    def test_carries_the_optional_fields(self):
        self.assertEqual(canon_state("ok", canonical_sha256="deadbeef", duration_s=30.0,
                                     checked_at="2026-08-25T00:00:00Z"),
                         {"status": "ok", "reason": None, "canonical_sha256": "deadbeef",
                          "duration_s": 30.0, "checked_at": "2026-08-25T00:00:00Z"})

    def test_each_call_is_a_fresh_dict(self):
        a, b = canon_state("pending"), canon_state("pending")
        a["status"] = "ok"
        self.assertEqual(b["status"], "pending")


class MidiExtsTests(unittest.TestCase):
    def test_the_ingestable_extensions(self):
        self.assertEqual(MIDI_EXTS, {".mid", ".midi", ".rmi"})


if __name__ == "__main__":
    unittest.main()
