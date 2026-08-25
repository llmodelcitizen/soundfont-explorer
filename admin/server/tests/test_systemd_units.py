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


class RootInstallProvenance:
    """Whatever root installs into /etc/systemd/system or /usr/local/sbin must come from a
    copy the unprivileged app cannot have touched — never from $APP, the tree sfadmin owns
    and the network-facing app can write while it runs. That is the boundary sfadmin-update's
    own header states (#9); a unit taken from $APP is an ExecStart of the app's choosing,
    installed and started by root (#19)."""

    SCRIPT = ""
    ROOT_INSTALLS = ("/etc/systemd/system", "/usr/local/sbin/sfadmin-update")

    def setUp(self):
        self.body = script(self.SCRIPT)
        # comments talk about "$APP" on purpose; only the code is the boundary, and a
        # statement is what matters, so `\`-continuations are joined first
        joined = re.sub(r"\\\n\s*", " ", self.body)
        self.code = [ln.split("#", 1)[0].strip() for ln in joined.splitlines()]

    def stage_var(self) -> str:
        m = re.search(r"^(\w+)=\$\(mktemp -d", self.body, re.M)
        self.assertIsNotNone(m, f"no root-owned staging dir in {self.SCRIPT}")
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

    def install_statements(self) -> list:
        return [ln for ln in self.code
                if any(d in ln for d in self.ROOT_INSTALLS)
                and ln.startswith(("cp ", "install "))]

    def test_root_never_installs_out_of_the_sfadmin_owned_app_tree(self):
        hits = 0
        for ln in self.code:
            if not any(d in ln for d in self.ROOT_INSTALLS):
                continue
            hits += 1
            self.assertNotIn("$APP", ln, f"root installs from the sfadmin tree: {ln}")
            self.assertNotIn("as_sfadmin", ln, ln)
        self.assertGreaterEqual(hits, 2, "the install lines moved — this scan found nothing")

    def test_the_units_and_the_update_script_come_from_the_root_stage(self):
        stage = self.stage_var()
        copies = self.install_statements()
        self.assertEqual(len(copies), 2, copies)   # the units, and sfadmin-update
        for ln in copies:
            self.assertTrue(self.from_stage(ln, stage),
                            f"installed from outside the root stage: {ln}")


class UpdateScriptTests(RootInstallProvenance, unittest.TestCase):
    """POST /api/update is the normal deploy path; without a unit install there, a unit
    change only took effect after a full relaunch (bootstrap.sh) (#19)."""

    SCRIPT = "sfadmin-update"

    def test_units_are_reloaded_before_the_restart(self):
        self.assertIn("/etc/systemd/system", self.body)
        self.assertIn("systemctl daemon-reload", self.body)
        self.assertLess(self.body.index("systemctl daemon-reload"),
                        self.body.index("systemctl restart sfadmin.service"))

    def test_the_stage_is_filled_by_root_from_the_bundle(self):
        """Here the app tree is unpacked AS sfadmin, so the stage cannot be copied out of
        it at any point: root fetches the bundle itself."""
        stage = self.stage_var()
        fills = [ln for ln in self.code if re.search(rf"-C\s+\"?\{stage}", ln)]
        self.assertTrue(fills, "nothing fills the staging dir")
        for ln in fills:
            self.assertNotIn("as_sfadmin", ln, ln)
            self.assertNotIn("runuser", ln, ln)
            self.assertNotIn("$APP", ln, ln)

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


class BootstrapScriptTests(RootInstallProvenance, unittest.TestCase):
    """bootstrap.sh unpacks the bundle as root, so its copy is safe — but only until it
    hands $APP to sfadmin. On a REBOOT sfadmin.service starts alongside sfadmin-seed.service
    (both WantedBy=multi-user.target, nothing ordering them), so between that chown and the
    install there is a window in which the app can rewrite a unit for root to activate."""

    SCRIPT = "bootstrap.sh"

    def test_the_stage_is_taken_before_the_tree_is_handed_to_sfadmin(self):
        stage = self.stage_var()
        fills = [i for i, ln in enumerate(self.code)
                 if stage in ln and ln.startswith(("cp ", "mkdir "))
                 and not any(d in ln for d in self.ROOT_INSTALLS)]
        self.assertTrue(fills, "nothing fills the staging dir")
        chown = next(i for i, ln in enumerate(self.code)
                     if ln.startswith("chown -R sfadmin:sfadmin \"$APP\""))
        self.assertLess(max(fills), chown,
                        "the root copy is taken after $APP became sfadmin-writable")


if __name__ == "__main__":
    unittest.main()
