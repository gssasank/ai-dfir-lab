"""The optional subscription route (dfir-lab use subscription | duke).

Covers: the route state; both Claude Code configurations and switching
between them (idempotent, unrelated settings kept, never half-locked); the
first-run seeding; the protection of the sign-in credential (deny rules,
guard, redaction, problem report); the route-aware readiness check and
readiness code; the new messages; start and use on both routes; and the
upgrade of a 1.0.0 setup. Claude Code is the fake from helpers.py; the real
binary is used in test_e2e_claude.py.
"""

import hashlib
import json
import os
import random
import stat
import subprocess
import sys
import unittest

import helpers
from dfir_lab import (check, claude_env, configure_claude as cc, messages, readiness, redact,
                      route, subscription)
from dfir_lab.config import Config

GUARD = helpers.LAB / "libexec" / "pretool-guard"

PROTOCOL_SIFT_LIKE = {
    "permissions": {
        "allow": ["Bash(*)", "Read", "Write(./analysis/*)"],
        "deny": ["Read(./secrets/**)", "Bash(rm -rf:*)"],
        "ask": [],
        "defaultMode": "acceptEdits",
    },
    "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "echo stopped >> ./analysis/log"}]}],
    },
}

GATEWAY_LOCK = ("ANTHROPIC_BASE_URL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL", "IS_DEMO",
                "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def lock_state(settings):
    """'duke' when the whole gateway lock is present, 'subscription' when none
    of it is, 'half' otherwise."""
    env = settings.get("env", {})
    parts = ["apiKeyHelper" in settings, "ANTHROPIC_BASE_URL" in env, "IS_DEMO" in env,
             "ANTHROPIC_API_KEY" in env, "ANTHROPIC_AUTH_TOKEN" in env]
    if all(parts):
        return "duke"
    if not any(parts) and not any(n in env for n in GATEWAY_LOCK):
        return "subscription"
    return "half"


class RouteStateTests(helpers.LabTestCase):
    start_mock = False

    def test_default_is_duke(self):
        self.assertEqual(route.current(), "duke")

    def test_save_and_read(self):
        route.save("subscription")
        self.assertEqual(route.current(), "subscription")
        data = json.loads((self.home / "dfir-lab" / "route.json").read_text())
        self.assertEqual(data["route"], "subscription")
        route.save("duke")
        self.assertEqual(route.current(), "duke")

    def test_unknown_or_broken_file_means_duke(self):
        p = self.home / "dfir-lab" / "route.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        for text in ("{not json", '{"route": "gateway2"}', "[]", ""):
            p.write_text(text)
            self.assertEqual(route.current(), "duke", text)
        with self.assertRaises(ValueError):
            route.save("bedrock")


class EnvTests(helpers.LabTestCase):
    start_mock = False

    def setUp(self):
        super().setUp()
        self.cfg = Config()

    def test_subscription_settings_env(self):
        env = claude_env.settings_env(self.cfg, "subscription")
        for name in GATEWAY_LOCK + claude_env.PROVIDER_VARS:
            self.assertNotIn(name, env)
        self.assertEqual(env["ANTHROPIC_MODEL"], "sonnet")
        for name, value in claude_env.LOAD_REDUCTION_ENV.items():
            self.assertEqual(env[name], value)
        self.assertEqual(env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"], "1")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL"], "1")

    def test_duke_settings_env_unchanged(self):
        self.write_conf({("claude", "effort"): "low"})
        env = claude_env.settings_env(Config(), "duke")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], self.base_url)
        self.assertEqual(env["IS_DEMO"], "1")
        self.assertEqual(env["CLAUDE_CODE_EFFORT_LEVEL"], "low")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        self.assertEqual(env["ANTHROPIC_MODEL"], "mock-main")

    def test_subscription_model_from_lab_conf(self):
        self.write_conf({("subscription", "model"): "claude-sonnet-5-5"})
        self.assertEqual(claude_env.settings_env(Config(), "subscription")["ANTHROPIC_MODEL"],
                         "claude-sonnet-5-5")

    def test_subscription_process_env_removes_gateway_and_credentials(self):
        hostile = {n: "x" for n in GATEWAY_LOCK + claude_env.PROVIDER_VARS}
        hostile["PATH"] = "/usr/bin"
        env = claude_env.process_env(self.cfg, base=hostile, route="subscription")
        for name in GATEWAY_LOCK + claude_env.PROVIDER_VARS:
            self.assertNotIn(name, env)
        self.assertEqual(env["ANTHROPIC_MODEL"], "sonnet")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_extra_env_only_on_duke(self):
        self.write_conf({("claude", "extra_env"): "\n    API_TIMEOUT_MS=600000"})
        cfg = Config()
        self.assertEqual(claude_env.settings_env(cfg, "duke")["API_TIMEOUT_MS"], "600000")
        self.assertNotIn("API_TIMEOUT_MS", claude_env.settings_env(cfg, "subscription"))

    def test_browser_is_chromium_when_a_desktop_exists(self):
        stub = self.tmp / "chromium"
        stub.write_text("#!/bin/sh\nexit 0\n")
        os.chmod(stub, 0o755)
        old = claude_env.CHROMIUM_CANDIDATES
        claude_env.CHROMIUM_CANDIDATES = (str(stub),)
        try:
            env = claude_env.process_env(self.cfg, base={"DISPLAY": ":0"}, route="subscription")
            self.assertEqual(env["BROWSER"], str(stub))
            # No desktop: Claude Code prints the link instead; nothing is set.
            env = claude_env.process_env(self.cfg, base={}, route="subscription")
            self.assertNotIn("BROWSER", env)
            # The student's own choice wins.
            env = claude_env.process_env(self.cfg, base={"DISPLAY": ":0", "BROWSER": "mine"},
                                         route="subscription")
            self.assertEqual(env["BROWSER"], "mine")
            # Never on the Duke route.
            env = claude_env.process_env(self.cfg, base={"DISPLAY": ":0"}, route="duke")
            self.assertNotIn("BROWSER", env)
        finally:
            claude_env.CHROMIUM_CANDIDATES = old


class SwitchSettingsTests(helpers.LabTestCase):
    start_mock = False

    def setUp(self):
        super().setUp()
        self.cfg = Config()
        self.settings = self.claude_dir / "settings.json"
        self.libexec = "/usr/local/lib/dfir-lab/libexec"
        self.claude_dir.mkdir()
        self.settings.write_text(json.dumps(PROTOCOL_SIFT_LIKE, indent=2))

    def apply(self, r):
        return cc.apply(self.cfg, libexec=self.libexec, route=r)

    def load(self):
        return json.loads(self.settings.read_text())

    def assertUnrelatedKept(self, s):
        self.assertEqual(s["permissions"]["defaultMode"], "acceptEdits")
        for item in PROTOCOL_SIFT_LIKE["permissions"]["allow"]:
            self.assertIn(item, s["permissions"]["allow"])
        for item in PROTOCOL_SIFT_LIKE["permissions"]["deny"]:
            self.assertIn(item, s["permissions"]["deny"])
        self.assertEqual(s["hooks"]["Stop"], PROTOCOL_SIFT_LIKE["hooks"]["Stop"])

    def assertLabRestrictions(self, s):
        deny = s["permissions"]["deny"]
        for rule in ("Read(~/.config/dfir-lab/**)", "WebFetch", "WebSearch", "Agent", "Workflow",
                     "Read(~/.claude/.credentials.json)", "Edit(~/.claude/.credentials.json)"):
            self.assertIn(rule, deny)
        guard = [h for g in s["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(len(guard), 1)
        self.assertEqual(guard[0]["command"], self.libexec + "/pretool-guard")
        self.assertEqual(guard[0]["onFailure"], "block")
        for name, value in claude_env.LOAD_REDUCTION_ENV.items():
            self.assertEqual(s["env"][name], value)

    def test_subscription_configuration(self):
        self.apply("duke")
        changed, _, _ = self.apply("subscription")
        self.assertTrue(changed)
        s = self.load()
        self.assertEqual(lock_state(s), "subscription")
        self.assertNotIn("apiKeyHelper", s)
        self.assertEqual(s["env"]["ANTHROPIC_MODEL"], "sonnet")
        self.assertUnrelatedKept(s)
        self.assertLabRestrictions(s)

    def test_switch_back_restores_duke_exactly(self):
        self.apply("duke")
        duke_once = self.load()
        self.apply("subscription")
        self.apply("duke")
        self.assertEqual(self.load(), duke_once)
        self.assertEqual(lock_state(self.load()), "duke")
        self.assertUnrelatedKept(self.load())
        self.assertLabRestrictions(self.load())

    def test_idempotent_both_ways(self):
        for r in ("subscription", "duke"):
            self.apply(r)
            before = self.settings.read_bytes()
            changed, _, _ = self.apply(r)
            self.assertFalse(changed, r)
            self.assertEqual(self.settings.read_bytes(), before)
            self.assertTrue(cc.is_configured(self.load(), cc.fragment(self.cfg, self.libexec, r),
                                             r))

    def test_backup_taken_once_and_kept(self):
        self.apply("subscription")
        self.apply("duke")
        self.apply("subscription")
        backup = json.loads((self.claude_dir / "settings.json.pre-dfir-lab").read_text())
        self.assertEqual(backup, PROTOCOL_SIFT_LIKE)

    def test_never_half_locked(self):
        """Random sequences of switches, with Protocol SIFT reinstalling over
        the file now and then: after every step the file is one whole route."""
        rng = random.Random(510)
        for _ in range(40):
            if rng.random() < 0.2:
                self.settings.write_text(json.dumps(PROTOCOL_SIFT_LIKE))
            r = rng.choice(("duke", "subscription"))
            self.apply(r)
            s = self.load()
            self.assertEqual(lock_state(s), r)
            self.assertUnrelatedKept(s)
            self.assertLabRestrictions(s)

    def test_subscription_drops_foreign_credentials_in_settings(self):
        data = dict(PROTOCOL_SIFT_LIKE, apiKeyHelper="/home/x/other-helper",
                    env={"ANTHROPIC_API_KEY": "sk-other", "CLAUDE_CODE_USE_BEDROCK": "1",
                         "MY_SETTING": "kept"})
        self.settings.write_text(json.dumps(data))
        self.apply("subscription")
        s = self.load()
        self.assertEqual(lock_state(s), "subscription")
        self.assertNotIn("CLAUDE_CODE_USE_BEDROCK", s["env"])
        self.assertEqual(s["env"]["MY_SETTING"], "kept")

    def test_credential_rule_for_config_dir(self):
        # The tests set CLAUDE_CONFIG_DIR, so the sign-in lives there: an
        # absolute (//path) rule is added on both routes.
        cred = str(self.claude_dir / ".credentials.json")
        for r in ("duke", "subscription"):
            self.apply(r)
            deny = self.load()["permissions"]["deny"]
            self.assertIn("Read(/%s)" % cred, deny)
            self.assertIn("Edit(/%s)" % cred, deny)
            self.assertTrue(("Read(/%s)" % cred).startswith("Read(//"))
        os.environ.pop("CLAUDE_CONFIG_DIR")
        self.assertEqual(cc.credential_deny_rules(), [])


class FirstRunTests(helpers.LabTestCase):
    start_mock = False

    def test_subscription_seeds_onboarding_and_trust(self):
        gpath = self.claude_dir / ".claude.json"
        self.claude_dir.mkdir()
        gpath.write_text(json.dumps({"numStartups": 3, "oauthAccount": {"emailAddress": "a@b"},
                                     "projects": {"/other": {"x": 1}}}))
        ok, msg = cc.prepare_first_run(Config(), route="subscription")
        self.assertTrue(ok, msg)
        data = json.loads(gpath.read_text())
        for flag in ("hasCompletedOnboarding", "hasSeenAutoDefaultNudge", "hasSeenAutoDefaultNotice"):
            self.assertIs(data[flag], True, flag)
        self.assertTrue(data["projects"][str(self.case)]["hasTrustDialogAccepted"])
        self.assertEqual(data["numStartups"], 3)
        self.assertEqual(data["oauthAccount"], {"emailAddress": "a@b"})
        self.assertEqual(data["projects"]["/other"], {"x": 1})
        self.assertNotIn("theme", data)
        # Again: nothing to change.
        before = gpath.read_bytes()
        self.assertTrue(cc.prepare_first_run(Config(), route="subscription")[0])
        self.assertEqual(gpath.read_bytes(), before)

    def test_logout_reset_is_repaired(self):
        cc.prepare_first_run(Config(), route="subscription")
        gpath = self.claude_dir / ".claude.json"
        data = json.loads(gpath.read_text())
        data.pop("hasCompletedOnboarding")       # what /logout does
        gpath.write_text(json.dumps(data))
        cc.prepare_first_run(Config(), route="subscription")
        self.assertTrue(json.loads(gpath.read_text())["hasCompletedOnboarding"])

    def test_duke_route_only_trusts(self):
        ok, _ = cc.prepare_first_run(Config(), route="duke")
        self.assertTrue(ok)
        data = json.loads((self.claude_dir / ".claude.json").read_text())
        for flag in ("hasCompletedOnboarding", "hasSeenAutoDefaultNudge", "hasSeenAutoDefaultNotice"):
            self.assertNotIn(flag, data)
        self.assertTrue(data["projects"][str(self.case)]["hasTrustDialogAccepted"])


class GuardCredentialTests(helpers.LabTestCase):
    start_mock = False

    def call(self, tool, tool_input, config_dir=True):
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}
        if config_dir:
            env["CLAUDE_CONFIG_DIR"] = str(self.claude_dir)
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}
        return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)

    def assertDenied(self, r):
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        reason = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn("off limits", reason)
        self.assertNotIn(self.FAKE_TOKEN, r.stdout + r.stderr)

    def test_sign_in_file_and_tokens_blocked(self):
        cred = self.fake_sign_in()
        self.assertDenied(self.call("Read", {"file_path": str(cred)}))
        self.assertDenied(self.call("Bash", {"command": "cat ~/.claude/.credentials.json"}))
        self.assertDenied(self.call("Bash", {"command": "cat ~/.claude/.cred\"\"entials.json"}))
        self.assertDenied(self.call("Grep", {"pattern": "accessToken",
                                             "path": str(self.claude_dir / ".credentials.json")}))
        self.assertDenied(self.call("Bash", {"command": "claude setup-token"}))
        self.assertDenied(self.call("Bash", {"command": "echo %s | base64" % self.FAKE_TOKEN}))
        # The token itself is found even when the file is elsewhere than the
        # path in the command (here: the default ~/.claude, no CLAUDE_CONFIG_DIR).
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / ".credentials.json").write_text(json.dumps(
            {"claudeAiOauth": {"accessToken": "sk-ant-oat01-hometokenhometokenhome"}}))
        self.assertDenied(self.call("Write", {"file_path": "/tmp/x",
                                              "content": "sk-ant-oat01-hometokenhometokenhome"},
                                    config_dir=False))

    def test_normal_work_allowed(self):
        self.fake_sign_in()
        for tool, inp in (("Bash", {"command": "fls -r -o 2048 /cases/szechuan/disk/DC01.E01"}),
                          ("Read", {"file_path": "/cases/szechuan/ticket.md"}),
                          ("Bash", {"command": "claude auth status --text"})):
            r = self.call(tool, inp)
            self.assertEqual(r.returncode, 0, (tool, inp, r.stdout, r.stderr))


class RedactionTests(helpers.LabTestCase):
    start_mock = False

    def test_token_shapes_redacted(self):
        text = ("a sk-ant-oat01-AbC_d-123456789xyz b sk-ant-ort01-zzzzzzzzzzzzzzzz "
                "c sk-ant-api03-abcdefghijklmnop d sk-not-a-token")
        out = redact.redact(text)
        self.assertNotIn("sk-ant-oat01", out)
        self.assertNotIn("sk-ant-ort01", out)
        self.assertNotIn("sk-ant-api03", out)
        self.assertEqual(out.count(redact.CREDENTIAL_MARK), 3)
        self.assertIn("sk-not-a-token", out)

    def test_values_from_the_sign_in_file_redacted(self):
        odd = "oddtoken-without-the-usual-prefix-1234567890"
        self.fake_sign_in(token=odd)
        self.assertNotIn(odd, redact.redact("leak: %s" % odd))
        self.assertNotIn(odd, redact.redact("spaced: " + " ".join(odd)))


class ReadinessCodeTests(unittest.TestCase):
    SUB_READY = [(i, "NA" if i in (6, 7, 8, 9) else "PASS") for i in range(1, 12)]
    DUKE_READY = [(i, "PASS") for i in range(1, 12)]

    def test_duke_codes_unchanged(self):
        # Same formula as lab version 1.0.0 (no route in the payload).
        body = ",".join("%d=%s" % p for p in self.DUKE_READY)
        old = hashlib.sha256(("1.0.0|2026-11-16|" + body).encode()).hexdigest()[:10]
        self.assertEqual(readiness.compute("1.0.0", self.DUKE_READY, "2026-11-16"),
                         "DFIR-1.0.0-R-" + old)

    def test_subscription_code_records_route(self):
        code = readiness.compute("1.1.0", self.SUB_READY, "2026-11-16", "subscription")
        self.assertRegex(code, r"^DFIR-1\.1\.0-RS-[0-9a-f]{10}$")
        doc = {"version": "1.1.0", "date": "2026-11-16", "route": "subscription",
               "results": [{"id": i, "status": s} for i, s in self.SUB_READY]}
        ok, why = readiness.verify(code, doc)
        self.assertTrue(ok, why)
        self.assertIn("subscription route", why)
        # The same results claimed for the Duke route do not verify.
        self.assertFalse(readiness.verify(code, dict(doc, route="duke"))[0])
        self.assertFalse(readiness.verify(code.replace("-RS-", "-R-"), doc)[0])

    def test_ready_rules(self):
        self.assertTrue(readiness.is_ready(self.SUB_READY, "subscription"))
        self.assertFalse(readiness.is_ready(self.SUB_READY, "duke"))
        self.assertFalse(readiness.is_ready(self.DUKE_READY, "subscription"))
        bad = list(self.SUB_READY)
        bad[3] = (4, "NA")                        # NA outside 6-9
        self.assertFalse(readiness.is_ready(bad, "subscription"))
        bad = list(self.SUB_READY)
        bad[9] = (10, "SKIP")
        self.assertFalse(readiness.is_ready(bad, "subscription"))
        warn = list(self.SUB_READY)
        warn[0] = (1, "WARN")
        self.assertTrue(readiness.is_ready(warn, "subscription"))
        self.assertIn("-NS-", readiness.compute("1.1.0", bad, "2026-11-16", "subscription"))


class MessageTests(helpers.LabTestCase):
    start_mock = False

    # What Claude Code 2.1.322 printed (first three) or documents (the rest).
    SAMPLES = {
        "Not logged in · Please run /login": "sub_not_signed_in",
        "Failed to authenticate. API Error: 401 OAuth access token is invalid.":
            "sub_login_expired",
        "API Error: Connection refused — a firewall or proxy may be blocking it (ECONNREFUSED)":
            "sub_no_network",
        "Login expired · Please run /login": "sub_login_expired",
        "OAuth token revoked · Please run /login": "sub_login_expired",
        "You've hit your session limit · resets 3:45pm": "sub_usage_limit",
        "You've hit your weekly limit · resets Nov 18, 9am": "sub_usage_limit",
        "Usage limit reached · continuing automatically at 5pm": "sub_usage_limit",
        "API Error: Request rejected (429) · this may be a temporary capacity issue.": "sub_busy",
        "DONE": None,
    }

    def test_classify(self):
        for text, kind in self.SAMPLES.items():
            self.assertEqual(subscription.classify_text(text), kind, text)

    def test_every_message_renders(self):
        cfg = Config()
        for kind in ("sub_not_signed_in", "sub_wrong_login", "sub_login_expired",
                     "sub_usage_limit", "sub_no_network", "sub_busy"):
            what, todo = messages.render(kind, cfg, detail="DETAIL", host="api.anthropic.com",
                                         reason="did not answer in time")
            self.assertNotIn("{", what + todo, kind)
            self.assertTrue(what and todo, kind)
            self.assertNotIn("gateway", what.lower(), kind)
        what, todo = messages.render("sub_usage_limit", cfg, detail="You've hit your session "
                                     "limit · resets 3:45pm")
        self.assertIn("resets 3:45pm", what)
        self.assertIn("dfir-lab use duke", todo)
        what, todo = messages.render("sub_no_network", cfg, host="api.anthropic.com",
                                     reason="did not answer in time", detail="no answer within 8s")
        self.assertIn("needs no VPN", todo)
        self.assertNotIn("Connect to the VPN", todo)

    def test_status_parsing(self):
        st = subscription.parse_status('{"loggedIn": true, "authMethod": "claude.ai", '
                                       '"subscriptionType": "max", "loginExpiresAt": null}')
        self.assertTrue(st.signed_in)
        self.assertEqual(subscription.describe(st), "signed in with a Claude Max subscription")
        st = subscription.parse_status('{"loggedIn": true, "authMethod": "api_key_helper"}')
        self.assertFalse(st.signed_in)
        self.assertIn("not with a Claude subscription", subscription.describe(st))
        st = subscription.parse_status('{"loggedIn": true, "authMethod": "claude.ai", '
                                       '"loginExpiresAt": "2020-01-01T00:00:00Z"}')
        self.assertTrue(st.expired)
        self.assertFalse(st.signed_in)
        st = subscription.parse_status("garbage")
        self.assertFalse(st.ok)


class SubscriptionCheckTests(helpers.LabTestCase):
    """The readiness check on the subscription route (fake Claude Code, the
    mock server standing in for Anthropic's hosts in check 5)."""

    def setUp(self):
        super().setUp()
        self.set_route("subscription")

    def test_signed_in_is_ready(self):
        self.fake_sign_in()
        r = self.run_cli("check", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["route"], "subscription")
        status = {x["id"]: x["status"] for x in doc["results"]}
        self.assertEqual(status, {1: "PASS", 2: "PASS", 3: "PASS", 4: "PASS", 5: "PASS",
                                  6: "NA", 7: "NA", 8: "NA", 9: "NA", 10: "PASS", 11: "PASS"})
        self.assertTrue(doc["ready"])
        self.assertRegex(doc["code"], r"-RS-[0-9a-f]{10}$")
        names = {x["id"]: x["name"] for x in doc["results"]}
        self.assertEqual(names[4], "Sign-in")
        r4 = doc["results"][3]["result"]
        self.assertIn("signed in with a Claude Pro subscription", r4)
        self.assertIn("Claude Sonnet", r4)
        self.assertNotIn("student@example.edu", r.stdout)
        # The gateway was never contacted with the key (no /v1 request).
        log = self.mock_log.read_text() if self.mock_log.exists() else ""
        self.assertNotIn("/v1/", log)
        # Check 10 ran Claude Code with the subscription environment.
        env = dict(l[4:].split("=", 1) for l in self.fake_log.read_text().splitlines()
                   if l.startswith("ENV=") and "=" in l[4:])
        self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertNotIn("IS_DEMO", env)
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", env)
        self.assertEqual(env["ANTHROPIC_MODEL"], "sonnet")
        settings = json.loads((self.claude_dir / "settings.json").read_text())
        self.assertEqual(lock_state(settings), "subscription")
        # Text report: N/A lines, READY, route named, no token.
        txt = (self.home / "dfir-lab" / "readiness" / "latest.txt").read_text()
        self.assertIn("N/A", txt)
        self.assertIn("own Claude subscription", txt)
        self.assertNotIn(self.FAKE_TOKEN, txt)
        ok, why = readiness.verify(doc["code"], doc)
        self.assertTrue(ok, why)

    def test_human_output(self):
        self.fake_sign_in()
        r = self.run_cli("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(" 6  Gateway login     N/A   not applicable on the subscription route",
                      r.stdout)
        self.assertIn("READY - every check that applies to the subscription route passed",
                      r.stdout)

    def test_not_signed_in(self):
        r = self.run_cli("check", "--json")
        self.assertEqual(r.returncode, 1)
        doc = json.loads(r.stdout)
        res = {x["id"]: x for x in doc["results"]}
        self.assertEqual(res[4]["status"], "FAIL")
        self.assertIn("not signed in with your Claude subscription", res[4]["result"])
        self.assertIn("dfir-lab use subscription", res[4]["todo"])
        self.assertEqual(res[10]["status"], "SKIP")
        self.assertFalse(doc["ready"])
        self.assertRegex(doc["code"], r"-NS-")
        self.assertFalse(self.fake_log.exists())

    def test_expired_and_other_login(self):
        self.fake_sign_in()
        r = self.run_cli("check", "--only", "4", "--json",
                         env={"FAKE_CLAUDE_EXPIRES": '"2020-01-01T00:00:00Z"'})
        res = json.loads(r.stdout)["results"][0]
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("expired", res["result"])
        (self.claude_dir / ".credentials.json").unlink()
        r = self.run_cli("check", "--only", "4", "--json", env={"FAKE_CLAUDE_OTHER_AUTH": "api_key"})
        res = json.loads(r.stdout)["results"][0]
        self.assertEqual(res["status"], "FAIL")
        self.assertIn("not with a Claude subscription", res["result"])

    def test_anthropic_unreachable(self):
        self.fake_sign_in()
        self.write_conf({("subscription", "hosts"): "http://127.0.0.1:%d" % helpers.free_port()})
        r = self.run_cli("check", "--json")
        res = {x["id"]: x for x in json.loads(r.stdout)["results"]}
        self.assertEqual(res[5]["status"], "FAIL")
        self.assertIn("Cannot reach Anthropic's service", res[5]["result"])
        self.assertNotIn("gateway", res[5]["result"].lower())
        self.assertNotIn("Connect to the VPN", res[5]["todo"])
        self.assertEqual(res[10]["status"], "SKIP")

    def test_check_10_failures_explained(self):
        self.fake_sign_in()
        cases = {"notloggedin": "not signed in with your Claude subscription",
                 "expired": "sign-in has expired",
                 "limit": "reached its usage limit",
                 "offline": "Cannot reach Anthropic's service"}
        for mode, text in cases.items():
            r = self.run_cli("check", "--only", "10", "--json", env={"FAKE_CLAUDE_MODE": mode})
            res = json.loads(r.stdout)["results"][0]
            self.assertEqual(res["status"], "FAIL", mode)
            self.assertIn(text, res["result"], mode)
        r = self.run_cli("check", "--only", "10", "--json", env={"FAKE_CLAUDE_MODE": "limit"})
        res = json.loads(r.stdout)["results"][0]
        self.assertIn("resets 3:45pm", res["result"])
        self.assertIn("dfir-lab use duke", res["todo"])

    def test_quick_check_for_the_setup_script(self):
        r = self.run_cli("check", "--quick", "--json")
        doc = json.loads(r.stdout)
        self.assertEqual(doc["route"], "subscription")
        self.assertEqual([x["id"] for x in doc["results"]], [1, 2, 3, 4, 5])

    def test_setup_key_still_tests_against_the_gateway(self):
        r = self.store_key()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Your key is saved and working", r.stdout)
        self.assertIn("To use this key instead, run: dfir-lab use duke", r.stdout)
        self.assertEqual(route.current(), "subscription")


class UseCommandTests(helpers.LabTestCase):

    def settings(self):
        return json.loads((self.claude_dir / "settings.json").read_text())

    def test_status_default(self):
        r = self.run_cli("use", "--status")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AI access: your Duke AI Gateway key.", r.stdout)
        self.assertIn("claude auth logout", r.stdout)
        r = self.run_cli("use")
        self.assertIn("AI access: your Duke AI Gateway key.", r.stdout)

    def test_switch_to_signed_in_subscription_and_back(self):
        self.store_key()
        key_before = (self.home / ".config" / "dfir-lab" / "duke-api-key").read_bytes()
        cred = self.fake_sign_in()
        cred_before = cred.read_bytes()
        r = self.run_cli("use", "subscription")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("already signed in with a Claude Pro subscription", r.stdout)
        self.assertIn("Done: Claude Code is signed in with a Claude Pro subscription", r.stdout)
        self.assertIn("Claude Sonnet", r.stdout)
        self.assertEqual(route.current(), "subscription")
        self.assertEqual(lock_state(self.settings()), "subscription")
        gconf = json.loads((self.claude_dir / ".claude.json").read_text())
        self.assertTrue(gconf["hasCompletedOnboarding"])
        # Again: idempotent.
        before = (self.claude_dir / "settings.json").read_bytes()
        r = self.run_cli("use", "subscription")
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.claude_dir / "settings.json").read_bytes(), before)
        r = self.run_cli("use", "--status")
        self.assertIn("AI access: your own Claude subscription.", r.stdout)
        self.assertIn("Claude sign-in: signed in with a Claude Pro subscription.", r.stdout)
        # Back to Duke.
        r = self.run_cli("use", "duke")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Done: the lab now uses your Duke AI Gateway key again", r.stdout)
        self.assertEqual(route.current(), "duke")
        self.assertEqual(lock_state(self.settings()), "duke")
        r = self.run_cli("use", "duke")
        self.assertIn("already uses your Duke AI Gateway key", r.stdout)
        # Neither the key nor the sign-in was touched; nothing printed them.
        self.assertEqual((self.home / ".config" / "dfir-lab" / "duke-api-key").read_bytes(),
                         key_before)
        self.assertEqual(cred.read_bytes(), cred_before)
        everything = self.all_text_under(self.home / "dfir-lab")
        self.assertNotIn(self.FAKE_TOKEN.encode(), everything)
        self.assertNoKey(everything)

    def test_sign_in_needs_a_terminal(self):
        r = self.run_cli("use", "subscription")
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs a terminal", r.stderr)
        # The choice is stored and the settings switched, so a later sign-in is enough.
        self.assertEqual(route.current(), "subscription")
        self.assertEqual(lock_state(self.settings()), "subscription")

    def test_route_save_failure_keeps_settings_and_route_together(self):
        from dfir_lab import use
        cfg = Config()
        cc.apply(cfg, route="duke")
        duke = self.settings()
        (self.home / "dfir-lab").mkdir(exist_ok=True)
        (self.home / "dfir-lab" / "route.json").mkdir()     # cannot be written as a file
        from dfir_lab.errors import LabError
        with self.assertRaises(LabError):
            use.switch(cfg, "subscription")
        self.assertEqual(self.settings(), duke)
        self.assertEqual(route.current(), "duke")

    def test_bad_usage(self):
        r = self.run_cli("use", "bedrock")
        self.assertEqual(r.returncode, 2)
        r = self.run_cli("use", "duke", "--status")
        self.assertEqual(r.returncode, 2)


class SignInPtyTests(helpers.LabTestCase):
    """`dfir-lab use subscription` in a pseudo-terminal: our two or three
    lines, Claude Code's own sign-in, Chromium as the browser, confirmation."""

    def run_pty(self, args, answers=b"", env_extra=None, timeout=60):
        import pty
        import select
        import time
        env = dict(os.environ)
        env.update(self.env())
        env.update(env_extra or {})
        master, slave = pty.openpty()
        proc = subprocess.Popen([sys.executable, str(helpers.BIN / "dfir-lab")] + args,
                                stdin=slave, stdout=slave, stderr=slave, env=env,
                                cwd=str(self.tmp), start_new_session=True)
        os.close(slave)
        out = b""
        sent = False
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([master], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
                if answers and not sent and b"[Y/n]" in out:
                    os.write(master, answers)
                    sent = True
            elif proc.poll() is not None:
                break
        proc.wait(timeout=30)
        os.close(master)
        return proc.returncode, out.decode("utf-8", "replace")

    def chromium_stub(self):
        bindir = self.tmp / "bin"
        bindir.mkdir()
        stub = bindir / "chromium"
        calls = self.tmp / "chromium-calls.log"
        stub.write_text('#!/bin/sh\necho "$*" >> %s\n' % calls)
        os.chmod(stub, 0o755)
        return bindir, calls

    def test_use_subscription_signs_in(self):
        bindir, calls = self.chromium_stub()
        code, out = self.run_pty(["use", "subscription"], env_extra={
            "DISPLAY": ":0", "PATH": "%s:%s" % (bindir, os.environ.get("PATH", ""))})
        self.assertEqual(code, 0, out)
        self.assertIn("It uses your subscription's own allowance and needs no Duke key and no VPN",
                      out)
        self.assertIn("Chromium opens a Claude sign-in page", out)
        self.assertIn("If no browser opens, copy the link printed below", out)
        self.assertIn("Opening browser to sign in", out)
        self.assertIn("Login successful.", out)
        self.assertIn("Done: Claude Code is signed in with a Claude Pro subscription "
                      "(account: student@example.edu).", out)
        self.assertIn("It will use Claude Sonnet (the current Sonnet model).", out)
        self.assertIn("claude.com/cai/oauth/authorize", calls.read_text())
        auth = self.fake_auth_log.read_text()
        self.assertIn("AUTH=auth login --claudeai", auth)
        login_env = auth.split("AUTH=auth login --claudeai", 1)[1]
        self.assertIn("ENV=BROWSER=%s" % (bindir / "chromium"), login_env)
        self.assertNotIn("ENV=ANTHROPIC_BASE_URL=", login_env.split("AUTH=", 1)[0])
        self.assertNotIn(self.FAKE_TOKEN, out)

    def test_failed_sign_in(self):
        code, out = self.run_pty(["use", "subscription"], env_extra={"FAKE_CLAUDE_LOGIN": "fail"})
        self.assertEqual(code, 1, out)
        self.assertIn("Login failed", out)
        self.assertIn("not signed in with your Claude subscription", out)
        self.assertIn("dfir-lab use duke", out)

    def test_start_offers_sign_in(self):
        self.set_route("subscription")
        code, out = self.run_pty(["start"], answers=b"y\r",
                                 env_extra={"DFIR_LAB_NO_EXEC": "1"})
        self.assertEqual(code, 0, out)
        self.assertIn("not signed in with your Claude subscription yet", out)
        self.assertIn("Sign in to your Claude account now? [Y/n]", out)
        self.assertIn("Login successful.", out)
        self.assertIn("AI access: your own Claude subscription", out)
        self.assertIn("[no-exec] would run", out)
        self.assertNotIn("gateway", out.lower())


class StartSubscriptionTests(helpers.LabTestCase):

    def parse_fake_log(self):
        env = {}
        for line in self.fake_log.read_text().splitlines():
            if line.startswith("ENV="):
                k, _, v = line[4:].partition("=")
                env[k] = v
        return env

    def test_start_signed_in(self):
        self.set_route("subscription")
        self.fake_sign_in()
        hostile = {"ANTHROPIC_API_KEY": "sk-other-123456", "ANTHROPIC_BASE_URL": "http://x",
                   "CLAUDE_CODE_USE_VERTEX": "1"}
        r = self.run_cli("start", env=hostile)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AI access: your own Claude subscription (Claude Sonnet", r.stdout)
        self.assertIn("uses less of your subscription", r.stdout)
        self.assertIn("/clear", r.stdout)
        self.assertNotIn("long ones get slow", r.stdout)
        env = self.parse_fake_log()
        for name in GATEWAY_LOCK + ("CLAUDE_CODE_USE_VERTEX",):
            self.assertNotIn(name, env)
        self.assertEqual(env["ANTHROPIC_MODEL"], "sonnet")
        self.assertEqual(env["CLAUDE_CODE_DISABLE_WORKFLOWS"], "1")
        gconf = json.loads((self.claude_dir / ".claude.json").read_text())
        self.assertTrue(gconf["hasCompletedOnboarding"])
        self.assertTrue(gconf["projects"][str(self.case)]["hasTrustDialogAccepted"])
        self.assertIn("route=subscription",
                      (self.home / "dfir-lab" / "logs" / "sessions.log").read_text())

    def test_start_not_signed_in_without_terminal(self):
        self.set_route("subscription")
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not signed in with your Claude subscription", r.stdout + r.stderr)
        self.assertIn("dfir-lab use subscription", r.stderr)
        self.assertNotIn("gateway", (r.stdout + r.stderr).lower())
        self.assertFalse(self.fake_log.exists())

    def test_start_anthropic_unreachable_never_mentions_the_gateway(self):
        self.set_route("subscription")
        self.fake_sign_in()
        self.write_conf({("subscription", "hosts"): "http://127.0.0.1:%d" % helpers.free_port()})
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Cannot reach Anthropic's service", r.stderr)
        self.assertNotIn("gateway", (r.stdout + r.stderr).lower())
        self.assertFalse(self.fake_log.exists())

    def test_duke_banner_names_the_route_and_keeps_clear_advice(self):
        self.store_key()
        r = self.run_cli("start")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AI access: your Duke AI Gateway key", r.stdout)
        self.assertIn("short conversations are fast; long ones get slow.", r.stdout)
        self.assertEqual(self.parse_fake_log()["IS_DEMO"], "1")


class ProblemReportTests(helpers.LabTestCase):

    def test_report_on_subscription_route_has_no_credential(self):
        self.set_route("subscription")
        self.fake_sign_in()
        self.store_key()
        self.run_cli("check")
        r = self.run_cli("report-problem")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        report = next((self.home / "dfir-lab").glob("problem-report-*.txt")).read_text()
        self.assertIn("AI access route: own Claude subscription", report)
        self.assertIn("Claude sign-in: signed in with a Claude Pro subscription", report)
        self.assertNotIn(self.FAKE_TOKEN, report)
        self.assertNotIn("sk-ant-ort01", report)
        self.assertNotIn("student@example.edu", report)
        self.assertNoKey(report)
        self.assertNotIn("GET /v1/models", report)


class UpgradeFromOneZeroTests(helpers.LabTestCase):
    """A 1.0.0 setup (no route file, 1.0.0 settings, a stored key, progress
    and notes) after the 1.1.0 toolkit's installer runs over it. Staged with
    install.sh --root, like test_installer.py; the full upgrade with the real
    setup script is tested in a VM (vmtest/Test-Bootstrap.ps1, step UP)."""
    start_mock = False

    def setUp(self):
        super().setUp()
        import pwd
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.root = self.tmp / "root"
        self.root.mkdir()
        self.staged_home = self.root / pwd.getpwuid(os.getuid()).pw_dir.lstrip("/")
        self.libexec = str(self.root / "usr/local/lib/dfir-lab/libexec")

    def install(self):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
        return subprocess.run(["sh", str(helpers.LAB / "install.sh"), "--root", str(self.root),
                               "--user", self.user, "--conf", str(self.conf)],
                              capture_output=True, text=True, env=env, timeout=120)

    def test_installer_over_one_zero(self):
        # What the 1.0.0 merge wrote: the same, minus the sign-in deny rules.
        os.environ.pop("CLAUDE_CONFIG_DIR")
        frag = cc.fragment(Config(), self.libexec, "duke")
        frag["permissions"]["deny"] = [d for d in frag["permissions"]["deny"]
                                       if ".credentials.json" not in d]
        old = cc.merge(json.loads(json.dumps(PROTOCOL_SIFT_LIKE)), frag, "duke")
        cdir = self.staged_home / ".claude"
        cdir.mkdir(parents=True)
        (cdir / "settings.json").write_text(json.dumps(old, indent=2))
        kdir = self.staged_home / ".config" / "dfir-lab"
        kdir.mkdir(parents=True)
        (kdir / "duke-api-key").write_text(self.mock_key + "\n")
        os.chmod(kdir / "duke-api-key", 0o600)
        sdir = self.staged_home / "dfir-lab"
        sdir.mkdir()
        (sdir / "progress.json").write_text('{"solved": [1, 2]}\n')
        notes = self.case / "analysis"
        notes.mkdir()
        (notes / "my-notes.txt").write_text("notes\n")

        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("AI access: your Duke AI Gateway key", r.stdout)
        new = json.loads((cdir / "settings.json").read_text())
        # Still the Duke route, now with the sign-in rules; everything else as before.
        self.assertEqual(lock_state(new), "duke")
        self.assertEqual(new["apiKeyHelper"], old["apiKeyHelper"])
        self.assertEqual(new["env"], old["env"])
        self.assertEqual(new["hooks"], old["hooks"])
        self.assertEqual([d for d in new["permissions"]["deny"] if ".credentials.json" not in d],
                         old["permissions"]["deny"])
        self.assertIn("Read(~/.claude/.credentials.json)", new["permissions"]["deny"])
        self.assertEqual((kdir / "duke-api-key").read_text(), self.mock_key + "\n")
        self.assertEqual(stat.S_IMODE((kdir / "duke-api-key").stat().st_mode), 0o600)
        self.assertEqual((sdir / "progress.json").read_text(), '{"solved": [1, 2]}\n')
        self.assertEqual((notes / "my-notes.txt").read_text(), "notes\n")
        self.assertFalse((sdir / "route.json").exists())
        self.assertTrue((self.staged_home / "Desktop" / "dfir-lab-use-subscription.desktop")
                        .is_file())
        # A second run changes nothing.
        before = (cdir / "settings.json").read_bytes()
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual((cdir / "settings.json").read_bytes(), before)

    def test_installer_keeps_a_chosen_subscription_route(self):
        (self.staged_home / "dfir-lab").mkdir(parents=True)
        (self.staged_home / "dfir-lab" / "route.json").write_text('{"route": "subscription"}')
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = json.loads((self.staged_home / ".claude" / "settings.json").read_text())
        self.assertEqual(lock_state(s), "subscription")
        self.assertIn("AI access: your own Claude subscription", r.stdout)


if __name__ == "__main__":
    unittest.main()
