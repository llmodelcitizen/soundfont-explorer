"""The one timestamp format every admin record uses: UTC, whole seconds, literal Z.

library.json ``added_at`` / ``modified_at`` / ``updated_at`` / ``canon.checked_at``, run
records (``submitted_at``, ``finished_at``, ``finisher.ran_at``) and publish reports
(``checked_at``) all carry it, and renders.py parses it back with
``fromisoformat(s.replace("Z", "+00:00"))`` — keep the two in step.

Stdlib only: admin/scripts/ingest.py and the unit tests import it without FastAPI/boto3.
The catalog tools (catalog/_util.py) and the render image (render/sfr/jobs.py) carry the
same helper for their own deployables; this bundle does not ship those packages.
"""
from __future__ import annotations

import datetime

ISO_SECONDS = "%Y-%m-%dT%H:%M:%SZ"


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(ISO_SECONDS)
