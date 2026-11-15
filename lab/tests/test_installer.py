"""install.sh in a throwaway --root with --user set to the current user."""

import getpass
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import unittest

import helpers

INSTALL = helpers.LAB / "install.sh"


@unittest.skipUnless(shutil.which("sh") and shutil.which("getent"), "needs sh and getent")
class InstallerTests(helpers.LabTestCase):
    start_mock = False

    def setUp(self):
        super().setUp()
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.user_home = pwd.getpwuid(os.getuid()).pw_dir
        self.staged_home = self.root / self.user_home.lstrip("/")

    def sh(self, *args, env_extra=None):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        env.update(env_extra or {})
        # Deliberately no CLAUDE_CONFIG_DIR and no HOME pointing at the real home:
        # the installer must only touch the staged home.
        return subprocess.run(["sh", str(INSTALL)] + list(args), capture_output=True, text=True,
                              env=env, timeout=120)

    def install(self):
        return self.sh("--root", str(self.root), "--user", self.user, "--conf", str(self.conf))

    def test_shortcuts_trusted_through_running_session_bus(self):
        """With a desktop session running, gio must talk to that session's bus,
        not to a private dbus-run-session (whose second gvfsd-metadata lost the
        trusted flag on the SIFT VM)."""
        import socket
        runtime = self.tmp / "runtime"
        runtime.mkdir()
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(runtime / "bus"))
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir()
        log = self.tmp / "gio.log"
        for name, body in (("gio", 'echo "gio $* bus=$DBUS_SESSION_BUS_ADDRESS" >> "%s"\n' % log),
                           ("dbus-run-session", 'echo "dbus-run-session $*" >> "%s"\n' % log)):
            (fakebin / name).write_text("#!/bin/sh\n" + body)
            os.chmod(fakebin / name, 0o755)
        try:
            r = self.sh("--root", str(self.root), "--user", self.user, "--conf", str(self.conf),
                        env_extra={"PATH": "%s:%s" % (fakebin, os.environ.get("PATH", "/usr/bin:/bin")),
                                   "DFIR_LAB_RUNTIME_DIR": str(runtime)})
        finally:
            sock.close()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("(marked trusted)", r.stdout)
        lines = log.read_text().splitlines()
        self.assertEqual(len(lines), 5, lines)
        for line in lines:
            self.assertIn("metadata::trusted true", line)
            self.assertTrue(line.endswith("bus=unix:path=%s/bus" % runtime), line)
            self.assertFalse(line.startswith("dbus-run-session"), line)

    def test_install_twice_then_uninstall(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        bin_ = self.root / "usr/local/bin"
        lib = self.root / "usr/local/lib/dfir-lab"
        for p in (bin_ / "dfir-lab", bin_ / "dfir-lab-configure-claude",
                  lib / "dfir_lab" / "cli.py", lib / "libexec" / "key-helper",
                  lib / "libexec" / "pretool-guard", lib / "share" / "readiness-sample" / "readiness.sh",
                  lib / "share" / "claude" / "settings-fragment.json",
                  self.root / "etc/dfir-lab/lab.conf"):
            self.assertTrue(p.exists(), p)
        self.assertFalse((bin_ / "dfir-lab-make-checkpoints").exists())   # instructor tool
        self.assertTrue(os.access(lib / "libexec" / "key-helper", os.X_OK))
        self.assertEqual(stat.S_IMODE(os.stat(self.root / "etc/dfir-lab/lab.conf").st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(os.stat(self.staged_home / ".config/dfir-lab").st_mode), 0o700)
        for d in ("readiness", "logs"):
            self.assertTrue((self.staged_home / "dfir-lab" / d).is_dir())
        desk = self.staged_home / "Desktop" / "dfir-lab-check.desktop"
        text = desk.read_text()
        self.assertIn("Exec=%s/dfir-lab check --pause" % bin_, text)
        self.assertTrue(os.access(desk, os.X_OK))
        self.assertEqual(sorted(p.name for p in (self.staged_home / "Desktop").glob("dfir-lab-*.desktop")),
                         ["dfir-lab-check.desktop", "dfir-lab-replay.desktop",
                          "dfir-lab-setup-key.desktop", "dfir-lab-start.desktop",
                          "dfir-lab-use-subscription.desktop"])
        self.assertFalse((self.staged_home / "dfir-lab" / "submissions").exists())
        settings = self.staged_home / ".claude" / "settings.json"
        s = json.loads(settings.read_text())
        self.assertEqual(s["apiKeyHelper"], str(lib / "libexec" / "key-helper"))
        self.assertEqual(s["hooks"]["PreToolUse"][0]["hooks"][0]["command"],
                         str(lib / "libexec" / "pretool-guard"))
        first = settings.read_bytes()

        # The installed command works from the staged location.
        env = {"HOME": str(self.staged_home), "DFIR_LAB_CONF": str(self.root / "etc/dfir-lab/lab.conf"),
               "PATH": os.environ["PATH"]}
        v = subprocess.run([sys.executable, str(bin_ / "dfir-lab"), "version"], capture_output=True,
                           text=True, env=env)
        self.assertIn("lab version 0.1.0-test", v.stdout)
        key = helpers.random_key()
        subprocess.run([sys.executable, str(bin_ / "dfir-lab"), "setup-key", "--from-stdin"],
                       input=key, capture_output=True, text=True, env=env)
        kh = subprocess.run(["sh", str(lib / "libexec" / "key-helper")], capture_output=True,
                            text=True, env=env)
        self.assertEqual(kh.stdout, key + "\n")

        # Second run: same result, settings unchanged.
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(settings.read_bytes(), first)
        self.assertIn("already up to date", r.stdout)

        # Uninstall keeps the key and work, restores settings.
        r = self.sh("--root", str(self.root), "--user", self.user, "--uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((bin_ / "dfir-lab").exists())
        self.assertFalse(lib.exists())
        self.assertFalse((self.root / "etc/dfir-lab").exists())
        self.assertFalse(desk.exists())
        self.assertFalse(settings.exists())          # did not exist before install
        self.assertTrue((self.staged_home / ".config/dfir-lab/duke-api-key").exists())
        self.assertTrue((self.staged_home / "dfir-lab").is_dir())
        r = self.sh("--root", str(self.root), "--user", self.user, "--uninstall", "--purge")
        self.assertFalse((self.staged_home / ".config/dfir-lab").exists())
        self.assertFalse((self.staged_home / "dfir-lab").exists())

    def test_uninstall_restores_previous_settings(self):
        cdir = self.staged_home / ".claude"
        cdir.mkdir(parents=True)
        (cdir / "settings.json").write_text('{"model": "opus"}')
        self.assertEqual(self.install().returncode, 0)
        self.assertIn("apiKeyHelper", (cdir / "settings.json").read_text())
        self.sh("--root", str(self.root), "--user", self.user, "--uninstall")
        self.assertEqual((cdir / "settings.json").read_text(), '{"model": "opus"}')

    def test_usage_errors(self):
        self.assertEqual(self.sh().returncode, 2)
        self.assertEqual(self.sh("--user", self.user, "--bogus").returncode, 2)
        # Removed in spec 0.2: image export preparation.
        r = self.sh("--root", str(self.root), "--user", self.user, "--prepare-for-export",
                    "--yes-really")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown option", r.stderr)
        r = self.sh("--user", "no-such-user-xyz", "--root", str(self.root))
        self.assertEqual(r.returncode, 3)
        if os.getuid() != 0:
            r = self.sh("--user", self.user)            # real install needs root
            self.assertEqual(r.returncode, 3)
            self.assertIn("sudo", r.stderr)


if __name__ == "__main__":
    unittest.main()
