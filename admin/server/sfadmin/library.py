"""The MIDI library: in-memory view of library.json with S3-first mutations.

S3 is the source of truth. Every mutation updates the in-memory model, writes the object
change to S3 first (file bodies, then library.json with an ETag-conditional PUT), and only
then the local mirror — the local tree under SFADMIN_DATA is a boot-time cache. The
conditional PUT means a concurrent second writer (there should never be one: single admin,
single box) fails loudly instead of clobbering.

Ids are minted exactly the way canon.py derives them (import_labels), then pinned forever
in the entry — renames and moves never change an id, so renders survive reorganisation.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading

from .config import get_config

MIDI_EXTS = {".mid", ".midi", ".rmi"}
_SAFE_SEG = re.compile(r"^[^/\0]+$")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canon_modules():
    """songs/tools from the bundled repo snapshot (canon.py owns id derivation)."""
    tools = os.path.join(get_config().repo, "songs", "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import canon  # noqa: PLC0415
    import smf  # noqa: PLC0415
    return canon, smf


def clean_rel_path(path: str) -> str:
    """Normalize a client-supplied library-relative path; reject traversal and junk."""
    path = path.strip().strip("/").replace("\\", "/")
    segs = [s for s in path.split("/") if s not in ("", ".")]
    if not segs or any(s == ".." or not _SAFE_SEG.match(s) for s in segs):
        raise ValueError(f"bad path {path!r}")
    if os.path.splitext(segs[-1])[1].lower() not in MIDI_EXTS:
        raise ValueError("path must end in .mid/.midi/.rmi")
    return "/".join(segs)


def clean_dir(path: str) -> str:
    """Normalize a client-supplied directory path ('' = the library root)."""
    path = path.strip().strip("/").replace("\\", "/")
    segs = [s for s in path.split("/") if s not in ("", ".")]
    if any(s == ".." or not _SAFE_SEG.match(s) for s in segs):
        raise ValueError(f"bad directory {path!r}")
    return "/".join(segs)


class ConflictError(RuntimeError):
    """library.json changed underneath us — a second writer exists."""


class Library:
    def __init__(self) -> None:
        self.cfg = get_config()
        self.lock = threading.RLock()
        self.doc: dict = {"schema": 1, "updated_at": None, "entries": {}}
        self.etag: str | None = None
        self.load()

    # ------------------------------------------------------------ persistence

    def load(self) -> None:
        """Prefer S3 (fresh + ETag for conditional writes); fall back to the local mirror."""
        try:
            r = self.cfg.s3.get_object(Bucket=self.cfg.bucket, Key="library/library.json")
            self.doc = json.load(r["Body"])
            self.etag = r["ETag"]
            return
        except Exception:
            pass
        try:
            with open(self.cfg.library_json) as fh:
                self.doc = json.load(fh)
            self.etag = None
        except FileNotFoundError:
            pass  # empty library until ingest runs

    def _save(self) -> None:
        body = (json.dumps(self.doc, indent=1, sort_keys=True) + "\n").encode()
        kwargs = {"Bucket": self.cfg.bucket, "Key": "library/library.json",
                  "Body": body, "ContentType": "application/json"}
        if self.etag:
            kwargs["IfMatch"] = self.etag
        try:
            r = self.cfg.s3.put_object(**kwargs)
        except self.cfg.s3.exceptions.ClientError as e:  # pragma: no cover - boto shape
            code = e.response.get("Error", {}).get("Code")
            if code in ("PreconditionFailed", "ConditionalRequestConflict"):
                self.load()  # resync to the winner
                raise ConflictError("library.json changed in S3 — another writer exists; reloaded") from e
            raise
        self.etag = r.get("ETag")
        os.makedirs(os.path.dirname(self.cfg.library_json), exist_ok=True)
        with open(self.cfg.library_json, "wb") as fh:
            fh.write(body)

    # ------------------------------------------------------------ reads

    def entries(self) -> list[dict]:
        with self.lock:
            return sorted(self.doc["entries"].values(), key=lambda e: (e["path"], e["id"]))

    def get(self, sid: str) -> dict:
        with self.lock:
            e = self.doc["entries"].get(sid)
            if e is None:
                raise KeyError(sid)
            return e

    def local_path(self, sid: str) -> str:
        return os.path.join(self.cfg.library_dir, self.get(sid)["path"])

    # ------------------------------------------------------------ mutations

    def upload(self, rel_path: str, blob: bytes) -> dict:
        """Add one MIDI file at rel_path (already clean_rel_path'd)."""
        canon, smf = _canon_modules()
        with self.lock:
            if any(e["path"] == rel_path for e in self.doc["entries"].values()):
                raise FileExistsError(f"a library file already exists at {rel_path}")
            sha = hashlib.sha256(blob).hexdigest()
            dup_of = [e["id"] for e in self.doc["entries"].values() if e["sha256"] == sha]
            status, reason = "pending", None
            try:
                sid, title, _ = canon.import_labels("import/FILES/" + rel_path, smf.parse(blob), None)
            except Exception as e:
                status, reason = "unparsed", f"{type(e).__name__}: {e}"
                stem = re.sub(r"\.midi?$", "", rel_path, flags=re.I)
                parent, _, leaf = stem.rpartition("/")
                title = f"{parent}/{leaf}" if parent else leaf
                sid = canon.slug(title)
            base, n = sid, 2
            while sid in self.doc["entries"]:
                sid = f"{base}-{n}"
                n += 1
            ts = now_iso()
            entry = {
                "id": sid, "path": rel_path, "name": title.rpartition("/")[2],
                "sha256": sha, "size": len(blob),
                "composer": None, "sequencer": None, "source_url": None,
                "hidden": False, "inject": None, "trim": None, "notes": None,
                "added_at": ts, "modified_at": ts,
                "canon": {"status": status, "reason": reason, "canonical_sha256": None,
                          "duration_s": None, "checked_at": None},
            }
            self.cfg.s3.put_object(Bucket=self.cfg.bucket, Key="library/FILES/" + rel_path,
                                   Body=blob, ContentType="audio/midi")
            self.doc["entries"][sid] = entry
            self.doc["updated_at"] = ts
            try:
                self._save()
            except Exception:
                self.doc["entries"].pop(sid, None)
                raise
            dst = os.path.join(self.cfg.library_dir, rel_path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(blob)
            return {**entry, "duplicate_of": dup_of}

    def move(self, sid: str, new_path: str) -> dict:
        """Rename/move the file. The id is pinned — renders and published URLs survive."""
        with self.lock:
            entry = self.get(sid)
            old_path = entry["path"]
            if new_path == old_path:
                return entry
            if any(e["path"] == new_path for e in self.doc["entries"].values()):
                raise FileExistsError(f"a library file already exists at {new_path}")
            s3, bucket = self.cfg.s3, self.cfg.bucket
            s3.copy_object(Bucket=bucket, Key="library/FILES/" + new_path,
                           CopySource={"Bucket": bucket, "Key": "library/FILES/" + old_path})
            entry["path"] = new_path
            entry["modified_at"] = self.doc["updated_at"] = now_iso()
            try:
                self._save()
            except Exception:
                entry["path"] = old_path
                s3.delete_object(Bucket=bucket, Key="library/FILES/" + new_path)
                raise
            s3.delete_object(Bucket=bucket, Key="library/FILES/" + old_path)
            src = os.path.join(self.cfg.library_dir, old_path)
            dst = os.path.join(self.cfg.library_dir, new_path)
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
            self._forget_stale_canonical(old_path)
            return entry

    EDITABLE = ("name", "composer", "sequencer", "source_url", "notes", "hidden", "inject", "trim")
    REBUILD_KEYS = ("inject", "trim")  # changing these changes the canonical bytes → re-render

    def edit(self, sid: str, fields: dict) -> dict:
        bad = set(fields) - set(self.EDITABLE)
        if bad:
            raise ValueError(f"not editable: {sorted(bad)}")
        with self.lock:
            entry = self.get(sid)
            before = {k: entry[k] for k in fields}
            entry.update(fields)
            if any(entry[k] != before.get(k) for k in self.REBUILD_KEYS if k in fields):
                entry["canon"] = {"status": "pending", "reason": None, "canonical_sha256": None,
                                  "duration_s": None, "checked_at": None}
            entry["modified_at"] = self.doc["updated_at"] = now_iso()
            try:
                self._save()
            except Exception:
                entry.update(before)
                raise
            return entry

    def delete(self, sid: str) -> dict:
        """Remove the file and its entry. Published renders are cleaned up by publishops."""
        with self.lock:
            entry = self.get(sid)
            self.doc["entries"].pop(sid)
            self.doc["updated_at"] = now_iso()
            try:
                self._save()
            except Exception:
                self.doc["entries"][sid] = entry
                raise
            self.cfg.s3.delete_object(Bucket=self.cfg.bucket, Key="library/FILES/" + entry["path"])
            local = os.path.join(self.cfg.library_dir, entry["path"])
            if os.path.exists(local):
                os.remove(local)
            self._forget_stale_canonical(entry["path"])
            return entry

    def _forget_stale_canonical(self, old_rel: str) -> None:
        """canon.py mirrors the import tree under songs/rendered/ and never cleans up; a
        moved or deleted source leaves its old canonical MIDI behind — remove it.
        Same stem rule as canon.import_labels: only .mid/.midi is stripped (.rmi kept)."""
        stale = os.path.join(self.cfg.repo, "songs", "rendered",
                             re.sub(r"\.midi?$", "", old_rel, flags=re.I) + ".mid")
        if os.path.exists(stale):
            os.remove(stale)

    # ------------------------------------------------------------ bulk mutations
    # One library.json save per operation (2,800 conditional PUTs for a folder-wide edit
    # would be absurd). All ids are validated up front so a typo fails before any change;
    # per-file S3 work still happens per object, then the doc saves once.

    BULK_EDITABLE = ("composer", "sequencer", "source_url", "notes", "hidden")

    def _require_all(self, ids: list[str]) -> list[dict]:
        missing = [i for i in ids if i not in self.doc["entries"]]
        if missing:
            raise KeyError(f"unknown ids: {missing[:5]}" + ("…" if len(missing) > 5 else ""))
        return [self.doc["entries"][i] for i in ids]

    def bulk_edit(self, ids: list[str], fields: dict) -> dict:
        bad = set(fields) - set(self.BULK_EDITABLE)
        if bad:
            raise ValueError(f"not bulk-editable: {sorted(bad)}")
        with self.lock:
            entries = self._require_all(ids)
            before = [{k: e[k] for k in fields} for e in entries]
            ts = now_iso()
            for e in entries:
                e.update(fields)
                e["modified_at"] = ts
            self.doc["updated_at"] = ts
            try:
                self._save()
            except Exception:
                for e, b in zip(entries, before):
                    e.update(b)
                raise
            return {"edited": len(entries)}

    def bulk_move(self, ids: list[str], dest_dir: str) -> dict:
        """Move the files into dest_dir, keeping their filenames. Ids stay pinned."""
        dest = clean_dir(dest_dir)
        s3, bucket = self.cfg.s3, self.cfg.bucket
        with self.lock:
            entries = self._require_all(ids)
            moves = []
            taken = {e["path"] for e in self.doc["entries"].values()}
            for e in entries:
                new_path = (dest + "/" if dest else "") + e["path"].rpartition("/")[2]
                if new_path == e["path"]:
                    continue
                if new_path in taken:
                    raise FileExistsError(f"a library file already exists at {new_path}")
                taken.add(new_path)
                moves.append((e, e["path"], new_path))
            for e, old, new in moves:
                s3.copy_object(Bucket=bucket, Key="library/FILES/" + new,
                               CopySource={"Bucket": bucket, "Key": "library/FILES/" + old})
            ts = now_iso()
            for e, old, new in moves:
                e["path"] = new
                e["modified_at"] = ts
            self.doc["updated_at"] = ts
            try:
                self._save()
            except Exception:
                for e, old, new in moves:
                    e["path"] = old
                    s3.delete_object(Bucket=bucket, Key="library/FILES/" + new)
                raise
            for e, old, new in moves:
                s3.delete_object(Bucket=bucket, Key="library/FILES/" + old)
                src = os.path.join(self.cfg.library_dir, old)
                dst = os.path.join(self.cfg.library_dir, new)
                if os.path.exists(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                self._forget_stale_canonical(old)
            return {"moved": len(moves), "dest": dest}

    def bulk_delete(self, ids: list[str]) -> dict:
        with self.lock:
            entries = self._require_all(ids)
            removed = {e["id"]: e for e in entries}
            for sid in removed:
                self.doc["entries"].pop(sid)
            self.doc["updated_at"] = now_iso()
            try:
                self._save()
            except Exception:
                self.doc["entries"].update(removed)
                raise
            for e in removed.values():
                self.cfg.s3.delete_object(Bucket=self.cfg.bucket,
                                          Key="library/FILES/" + e["path"])
                local = os.path.join(self.cfg.library_dir, e["path"])
                if os.path.exists(local):
                    os.remove(local)
                self._forget_stale_canonical(e["path"])
            return {"deleted": len(removed)}

    def channels(self, sid: str) -> list[dict]:
        """Per-channel info for the inject quick-fix. Channels are 1-based (the inject-rule
        convention), tracks 0-based; `track` is the first track with note-ons on the
        channel, matching how default_drum_rules targets its rule."""
        _, smf = _canon_modules()
        import inject_programs as IP  # noqa: PLC0415  (path set up by _canon_modules)
        with open(self.local_path(sid), "rb") as fh:
            parsed = smf.parse(fh.read())
        missing = set(IP.check_programs(parsed))
        out = []
        for ch0, notes in sorted(IP.channels_with_notes(parsed).items()):
            track = next((ti for ti, tr in enumerate(parsed.tracks)
                          if any(e.kind == "channel" and e.channel == ch0 and e.is_note_on()
                                 for e in tr)), 0)
            out.append({"channel": ch0 + 1, "track": track, "notes": notes,
                        "missing_program": ch0 + 1 in missing})
        return out

    # ------------------------------------------------------------ canon runs

    def canon_run(self, only: list[str] | None = None) -> dict:
        """Regenerate the fragment and run canon.py --lenient (optionally --only ids),
        then fold the results back into entry.canon. Blocking; call from a worker."""
        cfg = self.cfg
        repo = cfg.repo
        # canon reads songs/import/FILES/<path>; bootstrap symlinks that to the library dir
        with self.lock:
            lib_json = cfg.library_json
        # niced: a full canon run pegs the CPU for minutes and interactive previews
        # (fluidsynth) should win that contest. stdout is inherited on purpose — the
        # per-song progress lines land in the sfadmin journal live (journalctl -f),
        # instead of sitting invisible in a capture buffer for the whole run; stderr
        # stays piped for error reporting.
        cmd = ["nice", "-n", "10", sys.executable,
               os.path.join(repo, "songs", "tools", "fragment.py"), "--library", lib_json]
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE, text=True, cwd=repo)
        cmd = ["nice", "-n", "10", sys.executable,
               os.path.join(repo, "songs", "tools", "canon.py"), "--lenient"]
        for sid in only or []:
            cmd += ["--only", sid]
        p = subprocess.run(cmd, stderr=subprocess.PIPE, text=True, cwd=repo)
        if p.returncode != 0:
            raise RuntimeError(f"canon.py failed: {p.stderr[-2000:]}")
        with open(os.path.join(repo, "songs", "canon-report.json")) as fh:
            refused = {r["id"]: r["reason"] for r in json.load(fh)["refused"]}
        with open(os.path.join(repo, "songs", "songs.json")) as fh:
            built = {e["id"]: e for e in json.load(fh)["songs"]}
        ts = now_iso()
        with self.lock:
            targets = only if only is not None else list(self.doc["entries"])
            for sid in targets:
                entry = self.doc["entries"].get(sid)
                if entry is None:
                    continue
                if sid in refused:
                    entry["canon"] = {"status": "refused", "reason": refused[sid],
                                      "canonical_sha256": None, "duration_s": None, "checked_at": ts}
                elif sid in built:
                    b = built[sid]
                    entry["canon"] = {"status": "ok", "reason": None,
                                      "canonical_sha256": b["sha256"],
                                      "duration_s": b["duration_s"], "checked_at": ts}
                elif entry["canon"]["status"] != "unparsed" and not entry["hidden"]:
                    entry["canon"] = {"status": "refused", "reason": "not produced by canon.py",
                                      "canonical_sha256": None, "duration_s": None, "checked_at": ts}
            self.doc["updated_at"] = ts
            self._save()
        counts = {"ok": 0, "refused": 0, "unparsed": 0, "pending": 0}
        result: dict = {"totals": counts}
        with self.lock:
            for e in self.doc["entries"].values():
                counts[e["canon"]["status"]] = counts.get(e["canon"]["status"], 0) + 1
            if only is not None:
                # a targeted run should report the tracks it ran, not the library totals
                result["ran"] = {sid: dict(self.doc["entries"][sid]["canon"])
                                 for sid in only if sid in self.doc["entries"]}
        return result


_library: Library | None = None
_library_lock = threading.Lock()


def get_library() -> Library:
    global _library
    with _library_lock:
        if _library is None:
            _library = Library()
        return _library
