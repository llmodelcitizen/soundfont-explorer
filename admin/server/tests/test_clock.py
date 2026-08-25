"""clock.now_iso: the one timestamp helper (issue #24) and the format renders.py parses back. Stdlib only."""
import datetime
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("SFADMIN_BUCKET", "test")
os.environ.setdefault("SFADMIN_HOSTNAME", "test")
from sfadmin import clock, library, publishops, renders  # noqa: E402  (routes_* need FastAPI)

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class NowIsoTests(unittest.TestCase):
    def test_format_and_round_trip(self):
        before = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        s = clock.now_iso()
        after = datetime.datetime.now(datetime.timezone.utc)
        self.assertRegex(s, ISO_RE)
        # renders.py reads submitted_at/finished_at back exactly like this
        parsed = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        self.assertEqual(parsed.tzinfo, datetime.timezone.utc)
        self.assertTrue(before <= parsed <= after, (before, s, after))

    def test_one_helper_shared_by_every_writer(self):
        for mod in (library, publishops, renders):
            self.assertIs(mod.now_iso, clock.now_iso, mod.__name__)
            self.assertFalse(hasattr(mod, "_now"), mod.__name__)


if __name__ == "__main__":
    unittest.main()
