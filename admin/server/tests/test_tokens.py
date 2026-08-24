"""Signed-cookie round trip. Stdlib only — runs in CI without the FastAPI stack."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sfadmin.tokens import sign, verify  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
