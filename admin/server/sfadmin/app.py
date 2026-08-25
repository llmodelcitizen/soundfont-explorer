"""FastAPI application: auth middleware, bootstrap status, SPA, lifecycle endpoints.

Middleware order (outermost first):
  1. auth — everything except /auth/*, /healthz needs a valid session cookie; browser
     navigations are redirected to /auth/login, API calls get 401 JSON.
  2. CSRF — non-GET requests must come from our own origin (Sec-Fetch-Site).
Routes that need the library respond 503 until bootstrap.sh reports ready; the SPA shows
its wait screen off /api/bootstrap in the meantime.
"""
from __future__ import annotations

import os
import pathlib

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import auth, bootstrapstate, routes_library, routes_publish, routes_runs
from .config import get_config
from .safepath import contained_file

OPEN_PATHS = ("/auth/", "/healthz")

app = FastAPI(title="sfadmin", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    if not any(path == p.rstrip("/") or path.startswith(p) for p in OPEN_PATHS):
        email = auth.session_email(request)
        if email is None:
            if path.startswith("/api/"):
                return JSONResponse({"error": "unauthenticated"}, status_code=401)
            return RedirectResponse("/auth/login", status_code=302)
        request.state.email = email
        # same-origin fence for state-changing requests; Sec-Fetch-Site is absent only in
        # non-browser clients, which can't ride a victim's cookies anyway
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sfs = request.headers.get("sec-fetch-site")
            if sfs not in (None, "same-origin", "none"):
                return JSONResponse({"error": "cross-site request refused"}, status_code=403)
    return await call_next(request)


app.include_router(auth.router)
app.include_router(routes_library.router)
app.include_router(routes_runs.router)
app.include_router(routes_publish.router)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/api/bootstrap")
def bootstrap_status() -> dict:
    return bootstrapstate.status()


@app.get("/api/me")
def me(request: Request) -> dict:
    return {"email": request.state.email, "hostname": get_config().hostname}


@app.post("/api/update")
def update() -> dict:
    """Ask the (root) sfadmin-update path unit to re-pull the app bundle and restart us."""
    pathlib.Path("/run/sfadmin/update-requested").touch()
    return {"ok": True, "msg": "update requested; the service restarts in a few seconds"}


@app.post("/api/shutdown")
def shutdown() -> JSONResponse:
    iid = bootstrapstate.instance_id()
    if iid is None:
        return JSONResponse({"error": "not on EC2 (no instance metadata)"}, status_code=500)
    import boto3
    boto3.client("ec2").terminate_instances(InstanceIds=[iid])
    return JSONResponse({"ok": True, "msg": f"terminating {iid}"})


# The run reconciler (and its no-live-run ⇒ CE-DISABLED invariant) must hold even if
# nobody opens the Renders tab: start the manager as soon as bootstrap reports ready.
def _kick_reconciler() -> None:
    import time

    from . import renders
    while True:
        try:
            if bootstrapstate.ready():
                if get_config().render_enabled:
                    renders.get_manager()
                return
        except Exception:
            return  # not a real deployment (tests, local run) — routes still work lazily
        time.sleep(10)


import threading  # noqa: E402

threading.Thread(target=_kick_reconciler, name="reconciler-kick", daemon=True).start()


# ---------------------------------------------------------------- SPA (built by deploy.sh)

WEB_DIST = os.environ.get("SFADMIN_WEB", "/opt/sfadmin/app/admin/web/dist")

if os.path.isdir(WEB_DIST):  # absent in unit tests
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_DIST, "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        # a file of the built SPA, or the SPA shell for client-side routes — never anything
        # outside WEB_DIST (see safepath: Starlette does not collapse `..` for us)
        f = contained_file(WEB_DIST, full_path)
        return FileResponse(f or os.path.join(WEB_DIST, "index.html"))
