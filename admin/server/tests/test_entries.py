"""The shared library.json entry pieces: one entry constructor, one `canon` block, one MIDI_EXTS.

The literals these replaced were written out six times (library.py ×4, ingest.py ×1 plus the
edit-reset); library.json is diffed by eye, so both the key set and the key order are part of
the file format and are pinned here.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sfadmin.entries import (CANON_KEYS, ENTRY_DEFAULTS, MIDI_EXTS,  # noqa: E402
                             backfill, canon_state, new_entry)


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


class NewEntryTests(unittest.TestCase):
    """One constructor for the three places an entry is minted (the server's upload, ingest.py,
    seed_library.py). A library entry is the only way a MIDI reaches songs.json, so a field one
    of them forgot would be a field the published song silently lost."""

    def entry(self, **meta):
        return new_entry("a-one", "a/one.mid", "one", sha256="ab" * 32, size=12,
                         added_at="2026-08-25T00:00:00Z", canon=canon_state("pending"), **meta)

    def test_a_fresh_upload_is_owner_supplied_with_no_metadata(self):
        e = self.entry()
        self.assertEqual(e["license"], "owner-supplied")
        self.assertEqual([e[k] for k in ("composer", "sequencer", "source_url", "notes",
                                         "inject", "trim", "license_fields",
                                         "extra_include_classes", "inject_note")], [None] * 9)
        self.assertIs(e["hidden"], False)
        self.assertEqual(e["modified_at"], e["added_at"])

    def test_the_publishing_fields_are_all_there(self):
        """canon.py resolves `license` in songs/licenses.json, fills the notice from
        `license_fields`, adds `extra_include_classes` to the default set and appends
        `inject_note` to `modifications`. All four used to live in songs/corpus.json."""
        for k in ("license", "license_fields", "extra_include_classes", "inject_note"):
            self.assertIn(k, ENTRY_DEFAULTS)

    def test_metadata_overrides_the_defaults(self):
        e = self.entry(composer="Scott Joplin", license="mutopia-pd",
                       license_fields={"mutopia_id": "Mutopia-2011/11/13-23"})
        self.assertEqual(e["composer"], "Scott Joplin")
        self.assertEqual(e["license"], "mutopia-pd")
        self.assertEqual(e["license_fields"], {"mutopia_id": "Mutopia-2011/11/13-23"})

    def test_a_field_that_is_not_an_entry_field_is_a_typo(self):
        with self.assertRaises(ValueError):
            self.entry(licence="cc0")

    def test_backfill_only_adds_what_is_missing(self):
        old = self.entry(composer="X")
        for k in ("license", "license_fields", "inject_note"):
            del old[k]
        self.assertEqual(sorted(backfill(old)), ["inject_note", "license", "license_fields"])
        self.assertEqual(old["license"], "owner-supplied")
        self.assertEqual(old["composer"], "X")
        self.assertEqual(backfill(old), [])         # idempotent


class MidiExtsTests(unittest.TestCase):
    def test_the_ingestable_extensions(self):
        self.assertEqual(MIDI_EXTS, {".mid", ".midi", ".rmi"})


if __name__ == "__main__":
    unittest.main()
