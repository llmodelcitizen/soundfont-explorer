"""Phase-2 worker stub: validates the queue message protocol and the
job layout locally. The real SQS loop is not implemented in phase 1.

Message (JSON): {"ulid": "...", "midi_key": "uploads/<ulid>/song.mid", "email": "...", "submitted": "..."}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
MAX_DURATION_S = 360
MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class UploadJob:
    ulid: str
    midi_key: str
    email: str

    @staticmethod
    def parse(body: str) -> "UploadJob":
        d = json.loads(body)
        ulid = str(d.get("ulid", ""))
        if not ULID_RE.match(ulid):
            raise ValueError("bad ulid")
        key = str(d.get("midi_key", ""))
        if key != f"uploads/{ulid}/song.mid":
            raise ValueError("midi_key does not match ulid")
        email = str(d.get("email", ""))
        if "@" not in email:
            raise ValueError("bad email")
        return UploadJob(ulid, key, email)

    @property
    def public_prefix(self) -> str:
        return f"u/{self.ulid}/"


def run(queue_url: str | None) -> int:
    print("sfr worker: phase 2 is design-only in this release.")
    print(f"  queue: {queue_url or '(none)'}  max_duration_s={MAX_DURATION_S}  max_bytes={MAX_BYTES}")
    return 0
