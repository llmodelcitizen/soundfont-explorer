"""Guard rails on the systemd units bootstrap.sh installs (no systemd here — text checks)."""
import os
import unittest

UNITS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "systemd")


def unit(name: str) -> str:
    with open(os.path.join(UNITS, name)) as fh:
        return fh.read()


class CaddySyncTests(unittest.TestCase):
    def test_upload_sync_is_not_size_only(self):
        # a renewed cert/key is the same size as the pair it replaces; --size-only skipped
        # them and a relaunched box restored the expired pair (#19)
        exec_start = next(line for line in unit("sfadmin-caddy-sync.service").splitlines()
                          if line.startswith("ExecStart="))
        self.assertIn("aws s3 sync /var/lib/caddy", exec_start)
        self.assertNotIn("--size-only", exec_start)


if __name__ == "__main__":
    unittest.main()
