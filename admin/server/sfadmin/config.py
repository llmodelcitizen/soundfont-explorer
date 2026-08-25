"""Configuration: environment (written by bootstrap.sh to /etc/sfadmin.env) + SSM secrets.

Environment contract:
  SFADMIN_BUCKET    admin bucket name (library, runs, caddy state, app bundle)
  SFADMIN_HOSTNAME  public FQDN (admin.soundfonts.ericq.com)
  SFADMIN_REPO      repo snapshot root (/opt/sfadmin/app)
  SFADMIN_DATA      mutable data root (/opt/sfadmin/data: library/FILES, gm.sf2)
  SFADMIN_CACHE     preview cache dir (/var/cache/sfadmin)
  SFADMIN_STATUS    bootstrap status file (/run/sfadmin/bootstrap.json)
  AWS_DEFAULT_REGION

Secrets live in SSM SecureStrings under /soundfont-explorer/admin/ (github_client_id,
github_client_secret, session_key, allowed_emails) — created by hand, never in Terraform
state (docs/ADMIN.md). They are cached for SSM_TTL_S, not for the process lifetime: an
address removed from allowed_emails or a rotated session_key must take effect on the
running box without a restart (docs/ADMIN.md "Revoking access").
"""
from __future__ import annotations

import logging
import os
import threading
import time
from functools import lru_cache

SSM_PREFIX = "/soundfont-explorer/admin/"
SSM_TTL_S = float(os.environ.get("SFADMIN_SSM_TTL_S", "60"))

log = logging.getLogger("sfadmin.config")


def env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise RuntimeError(f"missing required environment variable {name}")
    return v


class Config:
    def __init__(self) -> None:
        self.bucket = env("SFADMIN_BUCKET")
        self.hostname = env("SFADMIN_HOSTNAME")
        self.repo = env("SFADMIN_REPO", "/opt/sfadmin/app")
        self.data = env("SFADMIN_DATA", "/opt/sfadmin/data")
        self.cache = env("SFADMIN_CACHE", "/var/cache/sfadmin")
        self.status_file = env("SFADMIN_STATUS", "/run/sfadmin/bootstrap.json")
        # render-fleet + site plumbing, baked into bundle.env by deploy.sh from
        # infra/live/outputs.json (which never reaches the box). Empty when the
        # fleet is disabled — render submission is then unavailable.
        self.site_bucket = os.environ.get("SFADMIN_SITE_BUCKET", "")
        self.distribution = os.environ.get("SFADMIN_DISTRIBUTION", "")
        self.fonts_bucket = os.environ.get("SFADMIN_FONTS_BUCKET", "")
        self.job_queue = os.environ.get("SFADMIN_JOB_QUEUE", "")
        self.job_definition = os.environ.get("SFADMIN_JOB_DEFINITION", "")
        self.compute_env = os.environ.get("SFADMIN_COMPUTE_ENV", "")
        self.log_group = os.environ.get("SFADMIN_LOG_GROUP", "")
        self._ssm_lock = threading.Lock()
        self._ssm: dict[str, tuple[float, str]] = {}   # name -> (fetched at, value)

    @property
    def render_enabled(self) -> bool:
        return bool(self.fonts_bucket and self.job_queue and self.job_definition
                    and self.compute_env and self.site_bucket)

    # boto3 clients are cheap to hold and thread-safe to use
    @property
    def s3(self):
        return _client("s3")

    @property
    def ssm(self):
        return _client("ssm")

    _now = staticmethod(time.monotonic)   # tests substitute a fake clock

    def _fetch_secret(self, name: str) -> str:
        p = self.ssm.get_parameter(Name=SSM_PREFIX + name, WithDecryption=True)
        return p["Parameter"]["Value"]

    def secret(self, name: str) -> str:
        """An SSM SecureString, re-read every SSM_TTL_S. A refresh that fails keeps serving
        the last good value (an SSM blip must not lock everyone out); only a first read
        with nothing cached raises."""
        with self._ssm_lock:
            hit = self._ssm.get(name)
            now = self._now()
            if hit is not None and now - hit[0] < SSM_TTL_S:
                return hit[1]
            try:
                value = self._fetch_secret(name)
            except Exception:
                if hit is None:
                    raise
                log.warning("SSM refresh of %s failed; keeping the cached value", name, exc_info=True)
                value = hit[1]
            self._ssm[name] = (now, value)
            return value

    @property
    def session_key(self) -> bytes:
        return self.secret("session_key").encode()

    @property
    def allowed_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.secret("allowed_emails").split(",") if e.strip()}

    @property
    def library_dir(self) -> str:
        return os.path.join(self.data, "library", "FILES")

    @property
    def library_json(self) -> str:
        return os.path.join(self.data, "library", "library.json")

    @property
    def gm_sf2(self) -> str:
        return os.path.join(self.data, "gm.sf2")


@lru_cache(maxsize=None)
def _client(service: str):
    import boto3  # deferred so stdlib-only unit tests can import sfadmin modules
    return boto3.client(service)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
