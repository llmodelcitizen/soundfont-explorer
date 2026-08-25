"""SSM values are cached for SSM_TTL_S, not forever (issue #7). Stdlib only."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sfadmin import config  # noqa: E402


class FakeConfig(config.Config):
    def __init__(self):
        with mock.patch.dict(os.environ, {"SFADMIN_BUCKET": "b", "SFADMIN_HOSTNAME": "h"}):
            super().__init__()
        self.values = {"allowed_emails": "a@x.io, B@x.io", "session_key": "k1"}
        self.fetches = 0
        self.fail = False
        self.clock = 1000.0

    def _now(self):
        return self.clock

    def _fetch_secret(self, name):
        if self.fail:
            raise RuntimeError("ssm down")
        self.fetches += 1
        return self.values[name]


class SsmTtlTests(unittest.TestCase):
    def setUp(self):
        self.cfg = FakeConfig()

    def test_cached_within_ttl_refetched_after(self):
        self.assertEqual(self.cfg.allowed_emails, {"a@x.io", "b@x.io"})
        self.cfg.clock += config.SSM_TTL_S / 2
        self.cfg.values["allowed_emails"] = "a@x.io"
        self.assertEqual(self.cfg.allowed_emails, {"a@x.io", "b@x.io"})   # still cached
        self.assertEqual(self.cfg.fetches, 1)
        self.cfg.clock += config.SSM_TTL_S
        self.assertEqual(self.cfg.allowed_emails, {"a@x.io"})             # revoked after the TTL
        self.assertEqual(self.cfg.fetches, 2)

    def test_key_rotation_is_picked_up(self):
        self.assertEqual(self.cfg.session_key, b"k1")
        self.cfg.values["session_key"] = "k2"
        self.cfg.clock += config.SSM_TTL_S + 1
        self.assertEqual(self.cfg.session_key, b"k2")

    def test_refresh_failure_keeps_last_value_first_read_raises(self):
        self.assertEqual(self.cfg.secret("session_key"), "k1")
        self.cfg.fail = True
        self.cfg.clock += config.SSM_TTL_S + 1
        with self.assertLogs("sfadmin.config", level="WARNING"):
            self.assertEqual(self.cfg.secret("session_key"), "k1")
        with self.assertRaises(RuntimeError):
            self.cfg.secret("github_client_id")


if __name__ == "__main__":
    unittest.main()
