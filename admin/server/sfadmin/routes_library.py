"""Library API: browse, upload, move, edit, hide, delete, canon runs, preview, zip."""
from __future__ import annotations

import hashlib
import os
import threading
import zipfile

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from . import bootstrapstate, preview
from .config import get_config
from .library import ConflictError, clean_rel_path, get_library, now_iso

router = APIRouter()


def _lib():
    if not bootstrapstate.ready():
        raise HTTPException(503, "still bootstrapping — try again shortly")
    return get_library()


def _wrap(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except KeyError as e:
        raise HTTPException(404, f"no such track {e}") from e
    except FileExistsError as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except ConflictError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/api/library")
def list_library() -> dict:
    lib = _lib()
    return {"updated_at": lib.doc.get("updated_at"), "entries": lib.entries(),
            "preview": preview.doctor()}


# ---- canon before /{sid} so the literal path wins ----

_canon_state: dict = {"running": False}
_canon_thread_lock = threading.Lock()


@router.post("/api/library/canon")
def canon_start(body: dict | None = None) -> dict:
    lib = _lib()
    only = (body or {}).get("ids")
    with _canon_thread_lock:
        if _canon_state.get("running"):
            raise HTTPException(409, "a canon run is already in progress")
        _canon_state.update(running=True, started_at=now_iso(), finished_at=None,
                            result=None, error=None, only=only)

        def work() -> None:
            try:
                _canon_state["result"] = lib.canon_run(only)
            except Exception as e:  # surfaced via status, not a dead thread
                _canon_state["error"] = str(e)
            finally:
                _canon_state.update(running=False, finished_at=now_iso())

        threading.Thread(target=work, name="canon-run", daemon=True).start()
    return _canon_state


@router.get("/api/library/canon/status")
def canon_status() -> dict:
    _lib()
    return _canon_state


# ---- file operations ----

@router.post("/api/library/upload")
def upload(files: list[UploadFile], dir: str = Form("")) -> dict:
    lib = _lib()
    results = []
    for f in files:
        rel = (dir.strip("/") + "/" if dir.strip("/") else "") + (f.filename or "upload.mid")
        try:
            rel = clean_rel_path(rel)
            entry = lib.upload(rel, f.file.read())
            results.append({"path": rel, "ok": True, "id": entry["id"],
                            "duplicate_of": entry.get("duplicate_of", [])})
        except (ValueError, FileExistsError, ConflictError) as e:
            results.append({"path": rel, "ok": False, "error": str(e)})
    return {"results": results}


@router.post("/api/library/bulk")
def bulk(body: dict) -> dict:
    """One call, one library.json save: {op: edit|move|delete, ids: [...], fields?|dir?}."""
    lib = _lib()
    ids = list(body.get("ids") or [])
    if not ids:
        raise HTTPException(400, "no ids")
    op = body.get("op")
    if op == "edit":
        return _wrap(lib.bulk_edit, ids, dict(body.get("fields") or {}))
    if op == "move":
        return _wrap(lib.bulk_move, ids, str(body.get("dir", "")))
    if op == "delete":
        return _wrap(lib.bulk_delete, ids)
    raise HTTPException(400, f"unknown op {op!r}")


@router.get("/api/library/{sid}/channels")
def channels(sid: str) -> list[dict]:
    return _wrap(_lib().channels, sid)


@router.post("/api/library/{sid}/move")
def move(sid: str, body: dict) -> dict:
    lib = _lib()
    new_path = _wrap(clean_rel_path, str(body.get("path", "")))
    return _wrap(lib.move, sid, new_path)


@router.patch("/api/library/{sid}")
def edit(sid: str, body: dict) -> dict:
    return _wrap(_lib().edit, sid, body)


@router.delete("/api/library/{sid}")
def delete(sid: str) -> dict:
    return _wrap(_lib().delete, sid)


# ---- preview + download ----

@router.get("/api/preview/{sid}.mp3")
def preview_mp3(sid: str):
    lib = _lib()
    entry = _wrap(lib.get, sid)
    hit = preview.cached(entry["sha256"])
    if hit:
        return FileResponse(hit, media_type="audio/mpeg")
    path = lib.local_path(sid)
    if not os.path.exists(path):
        raise HTTPException(404, f"{entry['path']} missing from the local mirror")
    try:
        return StreamingResponse(preview.stream(path, entry["sha256"]), media_type="audio/mpeg")
    except FileNotFoundError as e:
        raise HTTPException(503, str(e)) from e


@router.get("/api/library.zip")
def library_zip() -> FileResponse:
    """The whole MIDI library as one zip (built once per library version, then cached)."""
    lib = _lib()
    stamp = hashlib.sha256((lib.doc.get("updated_at") or "empty").encode()).hexdigest()[:16]
    out = os.path.join(get_config().cache, f"library-{stamp}.zip")
    if not os.path.exists(out):
        for f in os.listdir(get_config().cache):  # drop stale library zips
            if f.startswith("library-") and f.endswith(".zip"):
                os.remove(os.path.join(get_config().cache, f))
        tmp = out + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for e in lib.entries():
                src = lib.local_path(e["id"])
                if os.path.exists(src):
                    z.write(src, "FILES/" + e["path"])
        os.replace(tmp, out)
    return FileResponse(out, media_type="application/zip", filename="soundfont-explorer-midi-library.zip")
