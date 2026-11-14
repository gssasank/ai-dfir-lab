import json
import os
import stat

import helpers
from dfir_lab import casekit, claude_env
from dfir_lab.config import Config
from dfir_lab.errors import LabError


class StartTests(helpers.LabTestCase):
    def parse_fake_log(self):
        args, env, cwd = [], {}, None
        for line in self.fake_log.read_text().splitlines():
            if line.startswith("ARG="):
                args.append(line[4:])
            elif line.startswith("ENV="):
                k, _, v = line[4:].partition("=")
                env[k] = v
            elif line.startswith("CWD="):
                cwd = line[4:]
        return args, env, cwd

    def test_start_execs_claude_in_case_dir(self):
        self.store_key()
        r = self.run_cli("start", "--", "--resume", "abc",
                         env={"ANTHROPIC_API_KEY": "should-be-removed",
                              "ANTHROPIC_AUTH_TOKEN": "also-removed"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Case: Sample case", r.stdout)
        self.assertIn("dfir-lab checkpoint 1", r.stdout)
        args, env, cwd = self.parse_fake_log()
        self.assertEqual(args, ["--resume", "abc"])
        self.assertEqual(cwd, str(self.case))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], self.base_url)
        self.assertEqual(env["ANTHROPIC_MODEL"], "mock-main")
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn(self.mock_key, self.fake_log.read_text())
        for d in ("analysis", "reports", "exports"):
            self.assertTrue((self.case / d).is_dir())
        # Worksheet settings are unset by default: nothing copied, no banner line.
        self.assertFalse((self.case / "worksheet.md").exists())
        self.assertNotIn("Worksheet", r.stdout)
        log =(self.home / "dfir-lab" / "logs" / "sessions.log").read_text()
        self.assertIn("lab=0.1.0-test case=sample-case", log)
        self.assertTrue((self.claude_dir / "settings.json").is_file())
        # Evidence untouched and still read-only.
        ev = self.case / "evidence"
        self.assertEqual(sorted(p.name for p in ev.iterdir()),
                         ["security-events.csv", "system-events.csv"])
        self.assertFalse(os.stat(ev).st_mode & stat.S_IWUSR)

    def test_worksheet_copied_only_when_configured(self):
        self.store_key()
        self.write_conf({("case", "worksheet"): "worksheet.md",
                         ("case", "worksheet_template"): "worksheet.template.md"})
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ws = self.case / "worksheet.md"
        self.assertEqual(ws.read_text(), (self.case / "worksheet.template.md").read_text())
        self.assertIn("Worksheet: %s" % ws, r.stdout)
        # An existing worksheet is never overwritten.
        os.chmod(ws, 0o644)
        ws.write_text("my answers\n")
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(ws.read_text(), "my answers\n")

    def test_worksheet_template_alone_does_nothing(self):
        self.store_key()
        self.write_conf({("case", "worksheet_template"): "worksheet.template.md"})
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(p.name for p in self.case.glob("worksheet*")),
                         ["worksheet.template.md"])

    def test_no_key_non_interactive(self):
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No Duke AI key is stored", r.stdout)
        self.assertIn("no terminal", r.stderr)
        self.assertFalse(self.fake_log.exists())

    def test_unreachable_non_interactive(self):
        self.store_key()
        self.server.shutdown()
        self.server.server_close()
        self.server = None
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Cannot reach the gateway", r.stderr)
        self.assertIn("VPN (test hint)", r.stderr)
        self.assertFalse(self.fake_log.exists())

    def test_provider_and_credential_vars_removed(self):
        self.store_key()
        hostile = {n: "1" for n in claude_env.PROVIDER_VARS}
        hostile.update(ANTHROPIC_API_KEY="sk-other-123456", ANTHROPIC_AUTH_TOKEN="tok-other-1234")
        r = self.run_cli("start", env=hostile)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        _, env, _ = self.parse_fake_log()
        for name in claude_env.CONFLICTING_VARS:
            self.assertNotIn(name, env)
        self.assertEqual(env["IS_DEMO"], "1")

    # -- review H6 / spec 6.4 ------------------------------------------------
    def test_invalid_settings_stops_start(self):
        # Review P1, exactly: '{invalid' in the settings file, DFIR_LAB_NO_EXEC=1.
        self.store_key()
        self.claude_dir.mkdir()
        (self.claude_dir / "settings.json").write_text("{invalid")
        r = self.run_cli("start", env={"DFIR_LAB_NO_EXEC": "1"})
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertNotIn("would run", r.stdout)
        self.assertIn("not valid JSON", r.stderr)
        self.assertIn("Claude Code was not started", r.stderr)
        self.assertFalse(self.fake_log.exists())
        self.assertEqual((self.claude_dir / "settings.json").read_text(), "{invalid")

    @helpers.unittest.skipIf(os.getuid() == 0, "root ignores file modes")
    def test_unwritable_settings_stops_start(self):
        self.store_key()
        self.claude_dir.mkdir()
        self.claude_dir.chmod(0o555)
        try:
            r = self.run_cli("start")
        finally:
            self.claude_dir.chmod(0o755)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(self.fake_log.exists())

    # -- spec 6.4 minimum version -------------------------------------------
    def test_old_claude_version_refused(self):
        self.store_key()
        for version, code in (("2.1.294", 3), ("1.0.0", 3), ("garbage", 3), ("2.1.295", 0),
                              ("2.2.0", 0)):
            if self.fake_log.exists():
                self.fake_log.unlink()
            r = self.run_cli("start", env={"FAKE_CLAUDE_VERSION": version})
            self.assertEqual(r.returncode, code, (version, r.stdout, r.stderr))
            if code:
                self.assertIn("2.1.295", r.stderr, version)
                self.assertFalse(self.fake_log.exists(), version)
            else:
                self.assertTrue(self.fake_log.exists(), version)

    # -- spec 6.4 first interactive start -----------------------------------
    def test_start_pre_accepts_trust_for_case_folder(self):
        self.store_key()
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads((self.claude_dir / ".claude.json").read_text())
        self.assertIs(data["projects"][str(self.case)]["hasTrustDialogAccepted"], True)
        self.assertNotIn("trust this folder", r.stdout)

    def test_trust_not_seeded_banner_says_what_to_press(self):
        self.store_key()
        self.claude_dir.mkdir()
        (self.claude_dir / ".claude.json").write_text("{broken")
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('choose "Yes, I trust this folder"', r.stdout)
        self.assertEqual((self.claude_dir / ".claude.json").read_text(), "{broken")
        self.assertTrue(self.fake_log.exists())

    def test_missing_claude(self):
        self.store_key()
        self.write_conf({("claude", "binary"): "no-such-claude-binary"})
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 3)
        self.assertIn("Claude Code is not installed", r.stderr)


class EvidenceProtectionTests(helpers.LabTestCase):
    start_mock = False

    def test_is_evidence_path(self):
        entries = casekit.parse_manifest(self.case / "MANIFEST.sha256")
        self.assertTrue(casekit.is_evidence_path(self.case / "evidence", self.case, entries))
        self.assertTrue(casekit.is_evidence_path(self.case / "evidence" / "new.txt", self.case,
                                                 entries))
        self.assertFalse(casekit.is_evidence_path(self.case / "analysis", self.case, entries))

    def test_refuses_output_dir_inside_evidence(self):
        os.chmod(self.case / "MANIFEST.sha256", 0o644)
        with open(self.case / "MANIFEST.sha256", "a") as fh:
            fh.write("%s  analysis/planted.txt\n" % ("0" * 64))
        with self.assertRaises(LabError) as cm:
            casekit.ensure_output_dirs(Config())
        self.assertIn("inside the evidence", cm.exception.what)
        self.assertFalse((self.case / "analysis").exists())


if __name__ == "__main__":
    helpers.unittest.main()
