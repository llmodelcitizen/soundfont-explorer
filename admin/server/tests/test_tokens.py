"""Signed-cookie round trip. Stdlib only — runs in CI without the FastAPI stack."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sfadmin.tokens import allowed_session_email, sign, verify  # noqa: E402

KEY = b"test-key"


class TokenTests(unittest.TestCase):
    def test_roundtrip(self):
        t = sign({"email": "a@b.c", "exp": time.time() + 60}, KEY)
        self.assertEqual(verify(t, KEY)["email"], "a@b.c")

    def test_expired(self):
        t = sign({"email": "a@b.c", "exp": time.time() - 1}, KEY)
        self.assertIsNone(verify(t, KEY))

    def test_missing_exp_is_expired(self):
        self.assertIsNone(verify(sign({"email": "a@b.c"}, KEY), KEY))

    def test_tampered_payload(self):
        t = sign({"email": "a@b.c", "exp": time.time() + 60}, KEY)
        body, mac = t.rsplit(".", 1)
        forged = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + mac
        self.assertIsNone(verify(forged, KEY))

    def test_wrong_key(self):
        t = sign({"email": "a@b.c", "exp": time.time() + 60}, KEY)
        self.assertIsNone(verify(t, b"other-key"))

    def test_garbage(self):
        for junk in ("", "x", "a.b", "\x00\xff.beef"):
            self.assertIsNone(verify(junk, KEY))


class AllowedSessionTests(unittest.TestCase):
    """The allow-list is enforced per request, not only at login (issue #7)."""

    def token(self, email="Bob@Example.com", exp=60):
        return sign({"email": email, "exp": time.time() + exp}, KEY)

    def test_allowed(self):
        self.assertEqual(allowed_session_email(self.token(), KEY, {"bob@example.com"}), "Bob@Example.com")

    def test_removed_from_allow_list_is_revoked(self):
        t = self.token()
        self.assertEqual(allowed_session_email(t, KEY, {"bob@example.com"}), "Bob@Example.com")
        self.assertIsNone(allowed_session_email(t, KEY, {"alice@example.com"}))
        self.assertIsNone(allowed_session_email(t, KEY, set()))

    def test_invalid_cookie(self):
        allowed = {"bob@example.com"}
        self.assertIsNone(allowed_session_email(None, KEY, allowed))
        self.assertIsNone(allowed_session_email("", KEY, allowed))
        self.assertIsNone(allowed_session_email(self.token(exp=-1), KEY, allowed))
        self.assertIsNone(allowed_session_email(self.token(), b"other-key", allowed))
        self.assertIsNone(allowed_session_email(sign({"exp": time.time() + 60}, KEY), KEY, allowed))
        self.assertIsNone(allowed_session_email(sign({"email": 7, "exp": time.time() + 60}, KEY), KEY, allowed))


if __name__ == "__main__":
    unittest.main()
