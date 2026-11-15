import json
import os
import shlex
import stat
import unittest

import helpers
from dfir_lab import claude_env, configure_claude as cc
from dfir_lab.config import Config
from dfir_lab.errors import ConfigError

# Synthetic stand-in for the settings.json Protocol SIFT installs. It only has
# the same *shape* (permissions.allow/deny/ask/defaultMode, a Stop hook, no
# env); it is not a copy of the third-party file.
PROTOCOL_SIFT_LIKE = {
    "permissions": {
        "allow": ["Bash(*)", "Read", "Write(./analysis/*)", "Bash(exampletool *)"],
        "deny": ["Read(./secrets/**)", "Bash(rm -rf:*)", "WebFetch"],
        "ask": [],
        "defaultMode": "acceptEdits",
    },
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "echo stopped >> ./analysis/log"}]}],
    },
    "model": "opus",
}


class MergeTests(helpers.LabTestCase):
    start_mock = False

    def setUp(self):
        super().setUp()
        self.cfg = Config()
        self.settings = self.claude_dir / "settings.json"
        self.libexec = "/usr/local/lib/dfir-lab/libexec"

    def apply(self):
        return cc.apply(self.cfg, libexec=self.libexec)

    def load(self):
        return json.loads(self.settings.read_text())

    def test_no_existing_file(self):
        changed, path, merged = self.apply()
        self.assertTrue(changed)
        self.assertEqual(path, self.settings)
        s = self.load()
        self.assertEqual(s["apiKeyHelper"], self.libexec + "/key-helper")
        self.assertEqual(s["env"]["ANTHROPIC_BASE_URL"], self.base_url)
        self.assertEqual(s["env"]["ANTHROPIC_MODEL"], "mock-main")
        self.assertEqual(s["env"]["ANTHROPIC_DEFAULT_HAIKU_MODEL"], "mock-small")
        self.assertEqual(s["env"]["CLAUDE_CODE_SUBAGENT_MODEL"], "mock-main")
        self.assertEqual(s["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
        self.assertEqual(s["env"]["DISABLE_AUTOUPDATER"], "1")
        self.assertIn("Read(~/.config/dfir-lab/**)", s["permissions"]["deny"])
        self.assertIn("WebSearch", s["permissions"]["deny"])
        hook = s["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "*")
        self.assertEqual(hook["hooks"][0]["command"], self.libexec + "/pretool-guard")
        self.assertTrue((self.claude_dir / "settings.json.pre-dfir-lab.absent").exists())
        self.assertNotIn(self.base_url.encode() + b"/v1", self.settings.read_bytes())

    def test_empty_file(self):
        self.claude_dir.mkdir()
        self.settings.write_text("")
        self.apply()
        self.assertEqual(self.load()["apiKeyHelper"], self.libexec + "/key-helper")
        self.assertEqual((self.claude_dir / "settings.json.pre-dfir-lab").read_text(), "")

    def test_protocol_sift_like_file(self):
        self.claude_dir.mkdir()
        self.settings.write_text(json.dumps(PROTOCOL_SIFT_LIKE, indent=2))
        original = self.settings.read_text()
        self.apply()
        s = self.load()
        # Unrelated keys preserved.
        self.assertEqual(s["model"], "opus")
        self.assertEqual(s["permissions"]["allow"], PROTOCOL_SIFT_LIKE["permissions"]["allow"])
        self.assertEqual(s["permissions"]["defaultMode"], "acceptEdits")
        self.assertEqual(s["hooks"]["Stop"], PROTOCOL_SIFT_LIKE["hooks"]["Stop"])
        # Lists unioned, existing order first, no duplicates.
        deny = s["permissions"]["deny"]
        self.assertEqual(deny[:3], PROTOCOL_SIFT_LIKE["permissions"]["deny"])
        self.assertEqual(deny.count("WebFetch"), 1)
        self.assertIn("WebSearch", deny)
        self.assertEqual(len(s["hooks"]["PreToolUse"]), 1)
        # Backup is the original file.
        self.assertEqual((self.claude_dir / "settings.json.pre-dfir-lab").read_text(), original)

    def test_our_values_win(self):
        self.claude_dir.mkdir()
        self.settings.write_text(json.dumps({"apiKeyHelper": "/other/helper",
                                             "env": {"ANTHROPIC_BASE_URL": "https://wrong",
                                                     "KEEP_ME": "1"}}))
        self.apply()
        s = self.load()
        self.assertEqual(s["apiKeyHelper"], self.libexec + "/key-helper")
        self.assertEqual(s["env"]["ANTHROPIC_BASE_URL"], self.base_url)
        self.assertEqual(s["env"]["KEEP_ME"], "1")

    def test_idempotent_and_backup_once(self):
        self.claude_dir.mkdir()
        self.settings.write_text(json.dumps(PROTOCOL_SIFT_LIKE))
        original = self.settings.read_text()
        self.assertTrue(self.apply()[0])
        first = self.settings.read_bytes()
        self.assertFalse(self.apply()[0])
        self.assertEqual(self.settings.read_bytes(), first)
        # A later change (e.g. new model in lab.conf) does not overwrite the backup.
        self.write_conf({("gateway", "model_main"): "mock-small"})
        self.cfg = Config()
        self.assertTrue(self.apply()[0])
        self.assertEqual((self.claude_dir / "settings.json.pre-dfir-lab").read_text(), original)
        self.assertTrue(cc.is_configured(self.load(), cc.fragment(self.cfg, self.libexec)))

    def test_extra_env(self):
        self.write_conf({("claude", "extra_env"): "\n    API_TIMEOUT_MS=600000"})
        cc.apply(Config(), libexec=self.libexec)
        self.assertEqual(self.load()["env"]["API_TIMEOUT_MS"], "600000")

    def test_restore(self):
        self.claude_dir.mkdir()
        self.settings.write_text(json.dumps(PROTOCOL_SIFT_LIKE))
        original = self.settings.read_text()
        self.apply()
        msg = cc.restore()
        self.assertIn("restored", msg)
        self.assertEqual(self.settings.read_text(), original)

    def test_restore_when_absent_before(self):
        self.apply()
        self.assertIn("did not exist", cc.restore())
        self.assertFalse(self.settings.exists())

    def test_invalid_json_untouched(self):
        self.claude_dir.mkdir()
        self.settings.write_text("{not json")
        r = self.run_cli(prog="dfir-lab-configure-claude")
        self.assertEqual(r.returncode, 3)
        self.assertIn("not valid JSON", r.stderr)
        self.assertEqual(self.settings.read_text(), "{not json")

    def test_cli(self):
        r = self.run_cli("--libexec", self.libexec, prog="dfir-lab-configure-claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Updated", r.stdout)
        r = self.run_cli("--libexec", self.libexec, prog="dfir-lab-configure-claude")
        self.assertIn("already up to date", r.stdout)

    def test_ensure_repairs_overwritten_file(self):
        self.apply()
        self.settings.write_text(json.dumps(PROTOCOL_SIFT_LIKE))   # another installer
        self.assertTrue(cc.ensure(self.cfg, quiet=True))
        self.assertIn("apiKeyHelper", self.load())

    # -- review H6 / spec 6.4: failure is distinguishable and fatal ---------
    def test_ensure_changed_unchanged_failed(self):
        self.assertIs(cc.ensure(self.cfg, quiet=True), True)      # written
        self.assertIs(cc.ensure(self.cfg, quiet=True), False)     # nothing to change
        self.settings.write_text("{invalid")                       # review P1
        with self.assertRaises(ConfigError) as cm:
            cc.ensure(self.cfg, quiet=True)
        self.assertEqual(cm.exception.code, 3)
        self.assertIn("not valid JSON", cm.exception.what)
        self.assertEqual(self.settings.read_text(), "{invalid")

    @unittest.skipIf(os.getuid() == 0, "root ignores file modes")
    def test_ensure_write_failure_raises(self):
        self.claude_dir.mkdir()
        self.claude_dir.chmod(0o555)
        try:
            with self.assertRaises(ConfigError) as cm:
                cc.ensure(self.cfg, quiet=True)
            self.assertIn("Could not update", cm.exception.what)
        finally:
            self.claude_dir.chmod(0o755)

    # -- review M11: no traceback from the configure command ----------------
    @unittest.skipIf(os.getuid() == 0, "root ignores file modes")
    def test_cli_permission_error_is_plain_exit_3(self):
        # Review P5, exactly: config directory mode 0555, no settings file.
        self.claude_dir.mkdir()
        self.claude_dir.chmod(0o555)
        try:
            r = self.run_cli(prog="dfir-lab-configure-claude")
        finally:
            self.claude_dir.chmod(0o755)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("Problem: Could not", r.stderr)
        self.assertFalse(self.settings.exists())

    @unittest.skipIf(os.getuid() == 0, "root ignores file modes")
    def test_cli_restore_permission_error_is_plain(self):
        self.apply()
        self.claude_dir.chmod(0o555)
        try:
            r = self.run_cli("--restore", prog="dfir-lab-configure-claude")
        finally:
            self.claude_dir.chmod(0o755)
        self.assertEqual(r.returncode, 3)
        self.assertNotIn("Traceback", r.stderr)

    # -- review M7: conflicting credentials and providers --------------------
    def test_conflicting_credentials_neutralised_in_settings(self):
        key = helpers.random_key()
        # Review P1, exactly: merge({'env': {'ANTHROPIC_API_KEY': K}}, fragment).
        merged = cc.merge({"env": {"ANTHROPIC_API_KEY": key}}, cc.fragment(self.cfg, self.libexec))
        self.assertEqual(merged["env"]["ANTHROPIC_API_KEY"], "")
        self.claude_dir.mkdir()
        self.settings.write_text(json.dumps({"env": dict(
            {n: "1" for n in claude_env.PROVIDER_VARS},
            ANTHROPIC_API_KEY=key, ANTHROPIC_AUTH_TOKEN="tok-123456789", KEEP_ME="yes")}))
        self.apply()
        env = self.load()["env"]
        for name in claude_env.CONFLICTING_VARS:
            self.assertEqual(env[name], "", name)
        self.assertEqual(env["KEEP_ME"], "yes")
        self.assertNotIn(key, self.settings.read_text())
        for name in ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"):
            self.assertIn(name, claude_env.PROVIDER_VARS)

    def test_conflicting_credentials_removed_from_launch_env(self):
        base = {n: "1" for n in claude_env.CONFLICTING_VARS}
        base["PATH"] = "/usr/bin"
        env = claude_env.process_env(self.cfg, base)
        for name in claude_env.CONFLICTING_VARS:
            self.assertNotIn(name, env)
        self.assertEqual(env["ANTHROPIC_BASE_URL"], self.base_url)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_extra_env_may_not_set_credentials(self):
        self.write_conf({("claude", "extra_env"): "\n    ANTHROPIC_AUTH_TOKEN=abc"})
        with self.assertRaises(ConfigError) as cm:
            cc.apply(Config(), libexec=self.libexec)
        self.assertIn("ANTHROPIC_AUTH_TOKEN", cm.exception.what)
        r = self.run_cli(prog="dfir-lab-configure-claude")
        self.assertEqual(r.returncode, 3)

    # -- review M6: the merge owns the guard hook ----------------------------
    def test_weaker_guard_entry_is_replaced(self):
        lib = "/opt/dfir lab/libexec"                 # a space, so the command is quoted
        frag = cc.fragment(self.cfg, lib)
        cmd = frag["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertNotEqual(shlex.split(cmd)[0], cmd)
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "/opt/audit.sh"}]}
        current = {"hooks": {"PreToolUse": [
            # Review P1, exactly: our command with matcher "Read".
            {"matcher": "Read", "hooks": [{"type": "command", "command": cmd}]},
            # Our command, right matcher, but no onFailure, plus an unrelated hook.
            {"matcher": "*", "hooks": [{"type": "command", "command": cmd, "timeout": 10},
                                       {"type": "command", "command": "/opt/log.sh"}]},
            # The same path, quoted differently.
            {"matcher": "*", "hooks": [{"type": "command", "command": '"%s"' % shlex.split(cmd)[0],
                                        "onFailure": "continue"}]},
            other,
        ]}}
        merged = cc.merge(current, frag)
        groups = merged["hooks"]["PreToolUse"]
        ours = frag["hooks"]["PreToolUse"][0]
        self.assertEqual(groups.count(ours), 1)
        guard_entries = [h for g in groups for h in g["hooks"]
                         if shlex.split(h["command"]) == shlex.split(cmd)]
        self.assertEqual(guard_entries, ours["hooks"])
        self.assertEqual(guard_entries[0]["onFailure"], "block")
        self.assertIn(other, groups)
        self.assertIn({"matcher": "*", "hooks": [{"type": "command", "command": "/opt/log.sh"}]},
                      groups)
        self.assertEqual([g.get("matcher") for g in groups], ["*", "Bash", "*"])
        self.assertEqual(cc.merge(merged, frag), merged)            # idempotent

    def test_correct_guard_entry_kept_in_place(self):
        frag = cc.fragment(self.cfg, self.libexec)
        ours = frag["hooks"]["PreToolUse"][0]
        first = {"matcher": "Edit", "hooks": [{"type": "command", "command": "/opt/x"}]}
        merged = cc.merge({"hooks": {"PreToolUse": [ours, first]}}, frag)
        self.assertEqual(merged["hooks"]["PreToolUse"], [ours, first])

    # -- spec 6.4: first interactive start -----------------------------------
    def test_first_start_settings(self):
        self.apply()
        self.assertEqual(self.load()["env"]["IS_DEMO"], "1")
        # No plugin-marketplace registration (a GitHub clone) on first start.
        self.assertEqual(
            self.load()["env"]["CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL"], "1")
        from dfir_lab.claude_env import process_env
        self.assertEqual(process_env(Config(), base={})[
            "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL"], "1")

    def test_effort_level_from_lab_conf(self):
        # Gateway proof 2026-11-14: CLAUDE_CODE_EFFORT_LEVEL=low made Claude Code
        # 2.1.322 send output_config.effort "low".
        self.apply()
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", self.load()["env"])   # default: unset
        self.write_conf({("claude", "effort"): "low"})
        cc.apply(Config(), libexec=self.libexec)
        self.assertEqual(self.load()["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "low")
        self.assertEqual(claude_env.process_env(Config(), base={})["CLAUDE_CODE_EFFORT_LEVEL"],
                         "low")

    def test_bad_effort_level_is_config_error(self):
        self.write_conf({("claude", "effort"): "turbo"})
        with self.assertRaises(ConfigError) as cm:
            cc.apply(Config(), libexec=self.libexec)
        self.assertIn("[claude] effort", cm.exception.what)

    def test_unused_tools_are_left_out_of_requests(self):
        # Gateway proof 2026-11-14: every token of every request counts against the
        # key's per-minute limit, so tools the lab never uses are switched off.
        self.apply()
        data = self.load()
        # The sub-agent tool too (approved 2026-11-14): each sub-agent sends its own
        # full-size requests against the per-minute token limit.
        for tool in ("Workflow", "ScheduleWakeup", "ReportFindings", "ListAgents", "Agent"):
            self.assertIn(tool, data["permissions"]["deny"])
        for name, value in claude_env.LOAD_REDUCTION_ENV.items():
            self.assertEqual(data["env"][name], value, name)
        env = claude_env.process_env(Config(), base={})
        self.assertEqual(env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"], "1")
        # Tools students use stay available.
        for tool in ("Bash", "Read", "Edit", "Write", "Skill"):
            self.assertNotIn(tool, data["permissions"]["deny"])

    def test_trust_case_folder(self):
        gpath = self.claude_dir / ".claude.json"
        self.claude_dir.mkdir()
        gpath.write_text(json.dumps({"userID": "abc", "numStartups": 3, "projects": {
            "/elsewhere": {"hasTrustDialogAccepted": False, "history": [1]},
            str(self.case): {"allowedTools": ["x"]}}}))
        ok, msg = cc.trust_case_folder(self.cfg)
        self.assertTrue(ok, msg)
        data = json.loads(gpath.read_text())
        self.assertEqual(data["userID"], "abc")
        self.assertEqual(data["numStartups"], 3)
        self.assertEqual(data["projects"]["/elsewhere"], {"hasTrustDialogAccepted": False,
                                                          "history": [1]})
        self.assertEqual(data["projects"][str(self.case)],
                         {"allowedTools": ["x"], "hasTrustDialogAccepted": True})
        before = gpath.read_bytes()
        self.assertTrue(cc.trust_case_folder(self.cfg)[0])
        self.assertEqual(gpath.read_bytes(), before)               # idempotent

    def test_trust_case_folder_new_file_private(self):
        ok, _ = cc.trust_case_folder(self.cfg)
        self.assertTrue(ok)
        gpath = self.claude_dir / ".claude.json"
        self.assertEqual(json.loads(gpath.read_text()),
                         {"projects": {str(self.case): {"hasTrustDialogAccepted": True}}})
        self.assertEqual(stat.S_IMODE(gpath.stat().st_mode), 0o600)

    def test_trust_without_config_dir_uses_home(self):
        os.environ.pop("CLAUDE_CONFIG_DIR")
        self.assertTrue(cc.trust_case_folder(self.cfg)[0])
        self.assertTrue((self.home / ".claude.json").is_file())

    def test_trust_leaves_invalid_file_alone(self):
        self.claude_dir.mkdir()
        (self.claude_dir / ".claude.json").write_text("{nope")
        ok, msg = cc.trust_case_folder(self.cfg)
        self.assertFalse(ok)
        self.assertIn("not valid JSON", msg)
        self.assertEqual((self.claude_dir / ".claude.json").read_text(), "{nope")

    def test_cli_seeds_trust(self):
        r = self.run_cli("--libexec", self.libexec, prog="dfir-lab-configure-claude")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("trusted", r.stdout)
        data = json.loads((self.claude_dir / ".claude.json").read_text())
        self.assertTrue(data["projects"][str(self.case)]["hasTrustDialogAccepted"])


if __name__ == "__main__":
    helpers.unittest.main()
