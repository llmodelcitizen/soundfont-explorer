"""Read the bootstrap progress file that admin/scripts/bootstrap.sh maintains.

The SPA polls /api/bootstrap and shows a wait screen until phase == "ready"; API routes
that need the library respond 503 until then.
"""
from __future__ import annotations

import json
import os

from .config import get_config


def status() -> dict:
    try:
        with open(get_config().status_file) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"phase": "starting", "pct": 0, "msg": "bootstrap has not reported yet"}
    except (OSError, ValueError) as e:
        return {"phase": "unknown", "pct": 0, "msg": f"unreadable status file: {e}"}


def ready() -> bool:
    return status().get("phase") == "ready"


def instance_id() -> str | None:
    """This instance's id, via IMDSv2 (for the UI shutdown button)."""
    import urllib.request
    try:
        tok = urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}), timeout=2).read().decode()
        return urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": tok}), timeout=2).read().decode()
    except OSError:
        return None
