"""Guard rails on the systemd units bootstrap.sh installs (no systemd here — text checks)."""
import os
import re
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
    """POST /api/update is the normal deploy path; without a unit install there, a unit
    change only took effect after a full relaunch (bootstrap.sh) (#19). Installing units
    from that path is only safe if root takes them from somewhere the app cannot write —
    the privilege boundary the script's own header states (#9)."""

    ROOT_INSTALLS = ("/etc/systemd/system", "/usr/local/sbin/sfadmin-update")

    def setUp(self):
        self.body = script("sfadmin-update")
        # comments talk about "$APP" on purpose; only the code is the boundary, and a
        # statement is what matters, so `\`-continuations are joined first
        joined = re.sub(r"\\\n\s*", " ", self.body)
        self.code = [ln.split("#", 1)[0].strip() for ln in joined.splitlines()]

    def stage_var(self) -> str:
        m = re.search(r"^(\w+)=\$\(mktemp -d", self.body, re.M)
        self.assertIsNotNone(m, "no root-owned staging dir in sfadmin-update")
        return "$" + m.group(1)

    def from_stage(self, stmt: str, stage: str) -> bool:
        """Does this statement's source come from the root stage? One level of indirection
        is followed: `V=("$STAGE"/…/*)` then `install … "${V[@]}"`."""
        if stage in stmt:
            return True
        for var in re.findall(r"\$\{(\w+)\[[@*]\]\}", stmt):
            if re.search(rf"^{var}=\(.*\{stage}", self.body, re.M):
                return True
        return False

    def test_units_are_reloaded_before_the_restart(self):
        self.assertIn("/etc/systemd/system", self.body)
        self.assertIn("systemctl daemon-reload", self.body)
        self.assertLess(self.body.index("systemctl daemon-reload"),
                        self.body.index("systemctl restart sfadmin.service"))

    def test_root_never_installs_out_of_the_sfadmin_owned_app_tree(self):
        """$APP is unpacked AS sfadmin and stays sfadmin-writable while the app runs (it is
        restarted only at the end), and the app is what triggers the update — so a unit
        copied from there is an ExecStart of the app's choosing, run as root."""
        hits = 0
        for ln in self.code:
            if not any(d in ln for d in self.ROOT_INSTALLS):
                continue
            hits += 1
            self.assertNotIn("$APP", ln, f"root installs from the sfadmin tree: {ln}")
            self.assertNotIn("as_sfadmin", ln, ln)
        self.assertGreaterEqual(hits, 2, "the install lines moved — this scan found nothing")

    def test_what_root_installs_is_fetched_by_root_from_the_bundle(self):
        stage = self.stage_var()
        fills = [ln for ln in self.code if re.search(rf"-C\s+\"?\{stage}", ln)]
        self.assertTrue(fills, "nothing fills the staging dir")
        for ln in fills:
            self.assertNotIn("as_sfadmin", ln, ln)   # root's own copy, or it is worthless
            self.assertNotIn("runuser", ln, ln)
        copies = [ln for ln in self.code
                  if any(d in ln for d in self.ROOT_INSTALLS)
                  and ln.startswith(("cp ", "install "))]
        self.assertEqual(len(copies), 2, copies)   # the units, and this script
        for ln in copies:
            self.assertTrue(self.from_stage(ln, stage),
                            f"installed from outside the root stage: {ln}")

    def test_the_script_reinstalls_itself_so_a_fix_reaches_a_running_box(self):
        # only bootstrap.sh ever installed /usr/local/sbin/sfadmin-update, so a change to
        # this script (the unit install above included) needed a full relaunch (#19)
        self.assertTrue(any("/usr/local/sbin/sfadmin-update" in ln
                            and ln.startswith("install ") for ln in self.code))
        # ... and not by writing over the inode bash is still reading this run from
        self.assertTrue(any(ln.startswith("mv /usr/local/sbin/sfadmin-update")
                            for ln in self.code))

    def test_a_dropped_unit_kind_cannot_abort_the_deploy_after_the_swap(self):
        # `cp a*.service a*.timer a*.path` under `set -e`: drop the one .timer and the cp
        # fails, after the tree swap and before the restart (#19)
        self.assertIn("shopt -s nullglob", self.body)
        self.assertNotIn("sfadmin*.timer", self.body)
        self.assertNotIn("sfadmin*.path", self.body)


if __name__ == "__main__":
    unittest.main()
