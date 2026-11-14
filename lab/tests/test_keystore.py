import os
import stat
import subprocess

import helpers
from dfir_lab import keystore, paths
from dfir_lab.errors import LabError

HELPER = helpers.LAB / "libexec" / "key-helper"


def mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


class KeystoreTests(helpers.LabTestCase):
    start_mock = False

    def test_save_modes_and_content(self):
        os.umask(0o022)
        key = helpers.random_key()
        p = keystore.save(key)
        self.assertEqual(p, self.home / ".config" / "dfir-lab" / "duke-api-key")
        self.assertEqual(mode(p.parent), 0o700)
        self.assertEqual(mode(p), 0o600)
        self.assertEqual(p.read_text(), key + "\n")
        self.assertEqual(keystore.load(), key)

    def test_save_fixes_loose_directory_mode(self):
        d = self.home / ".config" / "dfir-lab"
        d.mkdir(parents=True, mode=0o755)
        os.chmod(d, 0o755)
        keystore.save(helpers.random_key())
        self.assertEqual(mode(d), 0o700)

    def test_atomic_replace_leaves_no_temp_files(self):
        keystore.save(helpers.random_key())
        k2 = helpers.random_key()
        keystore.save(k2)
        files = os.listdir(paths.key_dir())
        self.assertEqual(files, ["duke-api-key"])
        self.assertEqual(keystore.load(), k2)

    def test_atomic_write_failure_keeps_old_key(self):
        k1 = helpers.random_key()
        keystore.save(k1)
        real_replace = os.replace
        def boom(*a, **k):
            raise OSError("disk full")
        os.replace = boom
        try:
            with self.assertRaises(OSError):
                keystore.save(helpers.random_key())
        finally:
            os.replace = real_replace
        self.assertEqual(keystore.load(), k1)
        self.assertEqual(os.listdir(paths.key_dir()), ["duke-api-key"])

    def test_normalize_strips_whitespace_and_quotes(self):
        self.assertEqual(keystore.normalize('  "sk-abcdefgh"\n'), "sk-abcdefgh")
        self.assertEqual(keystore.normalize("'sk-abcdefgh'"), "sk-abcdefgh")
        self.assertEqual(keystore.normalize("“sk-abcdefgh”"), "sk-abcdefgh")

    def test_each_rejection_rule(self):
        cases = {
            "": "empty",
            "sk-abc def ghij": "spaces",
            "sk-ab": "only 5 characters",
        }
        for value, phrase in cases.items():
            with self.assertRaises(LabError) as cm:
                keystore.validate(value)
            self.assertIn(phrase, cm.exception.what)
            self.assertIn("Copy the whole key", cm.exception.todo)

    def test_cli_rejections_store_nothing(self):
        for value, phrase in (("\n", "empty"), ("sk-abc def ghij\n", "spaces"),
                              ("'short'\n", "only 5 characters")):
            r = self.run_cli("setup-key", "--from-stdin", input=value)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn(phrase, r.stderr)
            self.assertIn("not saved", r.stderr)
            self.assertFalse(paths.key_file().exists())

    def test_clear(self):
        keystore.save(helpers.random_key())
        r = self.run_cli("setup-key", "--clear")
        self.assertEqual(r.returncode, 0)
        self.assertIn("deleted", r.stdout)
        self.assertFalse(paths.key_file().exists())
        r = self.run_cli("setup-key", "--clear")
        self.assertIn("No key was stored", r.stdout)

    def test_status_never_shows_key(self):
        r = self.run_cli("setup-key", "--status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No Duke AI key is stored", r.stdout)
        for _ in range(20):
            key = helpers.random_key()
            keystore.save(key)
            r = self.run_cli("setup-key", "--status")
            self.assertEqual(r.returncode, 0)
            self.assertNotIn(key, r.stdout + r.stderr)
            self.assertNotIn(key[:-4], r.stdout + r.stderr)
            self.assertIn("ending in ...%s" % key[-4:], r.stdout)
            self.assertIn("%d characters" % len(key), r.stdout)

    def test_status_works_without_conf(self):
        r = self.run_cli("setup-key", "--status", env={"DFIR_LAB_CONF": "/nonexistent"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("No Duke AI key", r.stdout)


class KeyHelperTests(helpers.LabTestCase):
    start_mock = False

    def run_helper(self):
        return subprocess.run(["sh", str(HELPER)], capture_output=True, text=True,
                              env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"})

    def test_prints_key_and_nothing_else(self):
        key = helpers.random_key()
        keystore.save(key)
        r = self.run_helper()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, key + "\n")
        self.assertEqual(r.stderr, "")

    def test_trims_crlf(self):
        key = helpers.random_key()
        keystore.save(key)
        paths.key_file().write_text(key + "\r\n")
        self.assertEqual(self.run_helper().stdout, key + "\n")

    def test_missing_key(self):
        r = self.run_helper()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertIn("dfir-lab setup-key", r.stderr)

    def test_unreadable_or_empty_key(self):
        keystore.save(helpers.random_key())
        paths.key_file().write_text("\n")
        r = self.run_helper()
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        if os.getuid() != 0:
            keystore.save(helpers.random_key())
            os.chmod(paths.key_file(), 0o000)
            r = self.run_helper()
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")
            os.chmod(paths.key_file(), 0o600)


if __name__ == "__main__":
    import unittest
    unittest.main()
