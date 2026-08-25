"""Guard rails on the systemd units bootstrap.sh installs (no systemd here — text checks)."""
import os
import unittest

ADMIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
UNITS = os.path.join(ADMIN, "systemd")


def unit(name: str) -> str:
    with open(os.path.join(UNITS, name)) as fh:
        return fh.read()


def script(name: str) -> str:
    with open(os.path.join(ADMIN, "scripts", name)) as fh:
        return fh.read()


class CaddySyncTests(unittest.TestCase):
    """A renewed cert and its regenerated key are the same size as the pair they replace,
    so --size-only skips them — in BOTH directions (#19)."""

    def test_upload_sync_is_not_size_only(self):
        exec_start = next(line for line in unit("sfadmin-caddy-sync.service").splitlines()
                          if line.startswith("ExecStart="))
        self.assertIn("aws s3 sync /var/lib/caddy", exec_start)
        self.assertNotIn("--size-only", exec_start)

    def test_bootstrap_restore_is_not_size_only(self):
        # bootstrap.sh runs on every boot (sfadmin-seed.service is WantedBy=multi-user),
        # so a size-only restore keeps an expired same-size pair and Caddy re-issues
        line = next(ln for ln in script("bootstrap.sh").splitlines()
                    if "aws s3 sync" in ln and "/caddy/" in ln and "/var/lib/caddy" in ln)
        self.assertNotIn("--size-only", line)


class UpdateScriptTests(unittest.TestCase):
    def test_update_installs_the_units_it_ships(self):
        # POST /api/update is the normal deploy path; without this a unit change only took
        # effect after a full relaunch (bootstrap.sh) (#19)
        body = script("sfadmin-update")
        self.assertIn("/etc/systemd/system/", body)
        self.assertIn("systemctl daemon-reload", body)
        self.assertLess(body.index("systemctl daemon-reload"),
                        body.index("systemctl restart sfadmin.service"))


if __name__ == "__main__":
    unittest.main()
