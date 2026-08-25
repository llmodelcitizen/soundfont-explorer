"""GitHub OAuth (authorization-code web flow) + HMAC-signed session cookies.

Deliberately stdlib for the protocol pieces: two urllib calls to GitHub, hmac/base64 for
the cookies. Only a GitHub account with a *verified* email on the SSM allowlist gets a
session, and the allowlist is re-checked on every request (config re-reads SSM every
SSM_TTL_S), so removing an address revokes access within a minute. Sessions are stateless
signed cookies — nothing to persist, nothing to leak on the ephemeral box.
"""
from __future__ import annotations

import hmac
import json
import secrets
import time
import urllib.parse
import urllib.request

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .config import get_config
from .tokens import allowed_session_email, sign as _sign, verify as _verify

SESSION_COOKIE = "sfadmin_s"
STATE_COOKIE = "sfadmin_o"
SESSION_TTL_S = 7 * 24 * 3600
STATE_TTL_S = 300

router = APIRouter()


# ---------------------------------------------------------------- sessions

def session_email(request: Request) -> str | None:
    cfg = get_config()
    return allowed_session_email(request.cookies.get(SESSION_COOKIE), cfg.session_key, cfg.allowed_emails)


def _set_cookie(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True, secure=True,
                    samesite="lax", path="/")


# ---------------------------------------------------------------- github calls

def _github_token(code: str) -> str:
    cfg = get_config()
    req = urllib.request.Request(
        "https://github.com/login/oauth/access_token",
        data=urllib.parse.urlencode({
            "client_id": cfg.secret("github_client_id"),
            "client_secret": cfg.secret("github_client_secret"),
            "code": code,
        }).encode(),
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        tok = json.load(r).get("access_token")
    if not tok:
        raise ValueError("GitHub did not return an access token")
    return tok


def _github_verified_emails(token: str) -> list[str]:
    req = urllib.request.Request(
        "https://api.github.com/user/emails",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "sfadmin"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return [e["email"].lower() for e in json.load(r) if e.get("verified")]


# ---------------------------------------------------------------- routes

@router.get("/auth/login")
def login() -> Response:
    cfg = get_config()
    state = secrets.token_urlsafe(24)
    q = urllib.parse.urlencode({
        "client_id": cfg.secret("github_client_id"),
        "redirect_uri": f"https://{cfg.hostname}/auth/callback",
        "scope": "user:email",
        "state": state,
    })
    resp = RedirectResponse(f"https://github.com/login/oauth/authorize?{q}", status_code=302)
    _set_cookie(resp, STATE_COOKIE,
                _sign({"state": state, "exp": time.time() + STATE_TTL_S}, cfg.session_key),
                STATE_TTL_S)
    return resp


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = "") -> Response:
    cfg = get_config()
    saved = _verify(request.cookies.get(STATE_COOKIE, ""), cfg.session_key)
    if not code or not saved or not hmac.compare_digest(saved.get("state", ""), state):
        return HTMLResponse("<h1>Login failed</h1><p>Bad or expired OAuth state — "
                            '<a href="/auth/login">try again</a>.</p>', status_code=400)
    try:
        emails = _github_verified_emails(_github_token(code))
    except Exception as e:
        return HTMLResponse(f"<h1>Login failed</h1><p>GitHub error: {e}</p>", status_code=502)
    allowed = sorted(set(emails) & cfg.allowed_emails)
    if not allowed:
        shown = ", ".join(emails) or "(no verified emails)"
        return HTMLResponse(f"<h1>Not allowed</h1><p>None of your verified GitHub emails "
                            f"({shown}) is on the allow list.</p>", status_code=403)
    now = time.time()
    resp = RedirectResponse("/", status_code=302)
    _set_cookie(resp, SESSION_COOKIE,
                _sign({"email": allowed[0], "iat": now, "exp": now + SESSION_TTL_S}, cfg.session_key),
                SESSION_TTL_S)
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


@router.get("/auth/logout")
def logout() -> Response:
    resp = RedirectResponse("/auth/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
