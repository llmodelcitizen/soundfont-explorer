"""_util.py: the shared timestamp helper (issue #24: one now_iso per deployable, not one per module)."""

import datetime as dt
import re
import unittest

from catalog import _util, adlbanks, build, sf2scan, variants

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
NOW = dt.datetime(2026, 8, 21, 13, 5, 9, 123456, tzinfo=dt.timezone.utc)


class NowIsoTests(unittest.TestCase):
    def test_format_is_utc_whole_seconds_z(self):
        self.assertEqual(_util.now_iso(NOW), "2026-08-21T13:05:09Z")   # microseconds dropped, literal Z
        self.assertEqual(_util.now_iso(dt.datetime(2000, 1, 2, tzinfo=dt.timezone.utc)), "2000-01-02T00:00:00Z")

    def test_default_is_the_current_utc_time(self):
        before = _util.utc_now().replace(microsecond=0)
        s = _util.now_iso()
        after = _util.utc_now()
        self.assertRegex(s, ISO_RE)
        parsed = dt.datetime.strptime(s, _util.ISO_SECONDS).replace(tzinfo=dt.timezone.utc)
        self.assertTrue(before <= parsed <= after, (before, parsed, after))
        self.assertEqual(_util.utc_now().tzinfo, dt.timezone.utc)

    def test_every_catalog_tool_uses_the_shared_helper(self):
        """The four generated_at writers import now_iso from _util instead of formatting their own."""
        for mod in (adlbanks, build, sf2scan, variants):
            self.assertIs(getattr(mod, "now_iso", None), _util.now_iso, mod.__name__)


if __name__ == "__main__":
    unittest.main()
