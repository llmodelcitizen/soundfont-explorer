"""Stateless signed tokens (session + OAuth state cookies). Stdlib only, so the unit
tests run without the FastAPI stack installed."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def sign(payload: dict, key: bytes) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    mac = hmac.new(key, body, hashlib.sha256).hexdigest()
    return body.decode() + "." + mac


def verify(token: str, key: bytes) -> dict | None:
    """The payload, or None for anything invalid: bad MAC, malformed, or expired."""
    try:
        body, mac = token.rsplit(".", 1)
        want = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(mac, want):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None
