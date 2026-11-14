"""End-to-end: real Claude Code against the mock gateway (readiness check 10).

Skipped automatically when Claude Code is not installed. Point
DFIR_LAB_E2E_CLAUDE at a claude binary to choose one explicitly.

Isolation: CLAUDE_CONFIG_DIR and HOME are temporary directories, so the
developer's real ~/.claude is never read or written, and the gateway URL is
the mock on 127.0.0.1.
"""

import json
import os
import shutil
import sys
import unittest

import helpers
from dfir_lab import check, configure_claude
from dfir_lab.config import Config


def find_claude():
    c = os.environ.get("DFIR_LAB_E2E_CLAUDE")
    if c and os.access(c, os.X_OK):
        return c
    return shutil.which("claude")


CLAUDE = find_claude()


class _RealClaudeSetup(helpers.LabTestCase):
    """Temporary HOME and CLAUDE_CONFIG_DIR, mock gateway, stored key. No tests."""
    claude_binary = CLAUDE

    def setUp(self):
        super().setUp()
        # Belt and braces: everything Claude Code might write goes to temp dirs.
        self.assertTrue(str(self.claude_dir).startswith(str(self.tmp)))
        self.assertTrue(str(self.home).startswith(str(self.tmp)))
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude_dir)
        os.environ["HOME"] = str(self.home)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        # Test isolation only: Claude Code registers its official plugin
        # marketplace (a network fetch outside the mock) on a machine's first
        # interactive launch. CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC does not
        # cover that (code.claude.com/docs/en/env-vars).
        os.environ["CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL"] = "1"
        # Claude Code runs key-helper and pretool-guard itself, so they need exec
        # bits even when the checkout lost them (e.g. cloned on Windows).
        libexec = self.tmp / "libexec"
        shutil.copytree(helpers.LAB / "libexec", libexec)
        for f in libexec.iterdir():
            os.chmod(f, 0o755)
        os.environ["DFIR_LAB_LIBEXEC"] = str(libexec)
        self.store_key()
        self.write_conf({("claude", "readiness_timeout_s"): "240",
                           ("claude", "binary"): CLAUDE})


@unittest.skipUnless(CLAUDE, "Claude Code is not installed (set DFIR_LAB_E2E_CLAUDE to a claude binary)")
class RealClaudeCodeTests(_RealClaudeSetup):

    def test_readiness_check_10_passes(self):
        cfg = Config()
        configure_claude.apply(cfg)
        ctx = check.run_checks(cfg, [10])
        r = ctx.results[10]
        log = [json.loads(l) for l in self.mock_log.read_text().splitlines()]
        self.assertEqual(r.status, "PASS", "%s / %s\nmock log:\n%s" % (
            r.result, r.todo, "\n".join(map(json.dumps, log[-20:]))))
        messages = [e for e in log if e.get("path") == "/v1/messages" and e.get("status") == 200]
        self.assertTrue(any("tool_use" in (e.get("reply") or []) for e in messages))
        self.assertNoKey(self.mock_log.read_text())
        # Nothing was written to a real home directory.
        self.assertTrue((self.claude_dir / "settings.json").is_file())

    def test_guard_hook_blocks_key_read(self):
        import mock_gateway
        import subprocess
        from dfir_lab.claude_env import process_env
        self.server.state.scenarios[:0] = mock_gateway.load_scenarios(
            [helpers.FIXTURE.parent / "guard-probe.json"])
        cfg = Config()
        configure_claude.apply(cfg)
        work = self.tmp / "probe"
        work.mkdir()
        r = subprocess.run([CLAUDE, "-p", "GUARD-PROBE: do what the scenario says.",
                            "--max-turns", "4", "--output-format", "json",
                            "--permission-mode", "dontAsk", "--allowedTools", "Bash",
                            "--no-session-persistence"],
                           cwd=str(work), env=process_env(cfg), capture_output=True, text=True,
                           timeout=240)
        self.assertNoKey(r.stdout + r.stderr)
        log = [json.loads(l) for l in self.mock_log.read_text().splitlines()]
        followups = [e for e in log if e.get("tool_result_seen")]
        self.assertTrue(followups, "Claude Code never sent the tool result back:\n%s\n%s"
                        % (r.stdout[-2000:], "\n".join(map(json.dumps, log[-10:]))))
        self.assertTrue(all(not e["tool_result_contains_key"] for e in followups))
        self.assertTrue(any(e["tool_result_blocked_by_guard"] for e in followups),
                        "\n".join(map(json.dumps, followups)))

    def test_conflicting_credentials_cannot_override_the_key(self):
        """Review M7: an ANTHROPIC_AUTH_TOKEN already in settings.json and a
        wrong ANTHROPIC_API_KEY in the shell that runs `claude` directly (not
        through dfir-lab start) must not replace the stored key."""
        import subprocess
        from dfir_lab.claude_env import process_env
        self.claude_dir.mkdir(exist_ok=True)
        (self.claude_dir / "settings.json").write_text(json.dumps(
            {"env": {"ANTHROPIC_AUTH_TOKEN": "sk-someone-elses-token-1"}}))
        cfg = Config()
        configure_claude.apply(cfg)
        env = process_env(cfg)
        env.update(ANTHROPIC_API_KEY="sk-wrong-key-from-shell-1",
                   ANTHROPIC_AUTH_TOKEN="sk-wrong-token-from-shell")
        work = self.tmp / "probe"
        work.mkdir()
        r = subprocess.run([CLAUDE, "-p", "Say hello.", "--max-turns", "1", "--output-format",
                            "json", "--no-session-persistence"], cwd=str(work), env=env,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
        self.assertIn("mock gateway", json.loads(r.stdout)["result"])
        log = [json.loads(l) for l in self.mock_log.read_text().splitlines()]
        self.assertFalse([e for e in log if e.get("status") == 401], log[-5:])


def _strip_ansi(data):
    import re
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", text)      # OSC
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", " ", text)             # CSI
    text = re.sub(r"\x1b[@-_]", "", text)
    return re.sub(r"[ \t]+", " ", text)


@unittest.skipUnless(CLAUDE, "Claude Code is not installed (set DFIR_LAB_E2E_CLAUDE to a claude binary)")
class FirstInteractiveStartTests(_RealClaudeSetup):
    """Spec 6.4: a student's first `dfir-lab start` reaches a ready prompt in
    the case folder with no theme picker and no workspace-trust question.

    Runs the real `dfir-lab start` in a pseudo-terminal with a fresh HOME and
    CLAUDE_CONFIG_DIR (no ~/.claude.json at all), lets it exec real Claude
    Code, waits for the screen to settle, types a prompt and checks that the
    mock gateway's reply appears. Either screen would swallow that input:
    the theme picker takes Enter as "choose this theme", the trust question
    takes Enter as "No, exit"."""

    THEME_SCREEN = ("Choose the text style", "Let's get started")
    TRUST_SCREEN = ("trust this folder", "Quick safety check", "Accessing workspace")

    def _read(self, fd, until, timeout, quiet=None):
        import select
        import time
        data = b""
        end = time.monotonic() + timeout
        last = time.monotonic()
        while time.monotonic() < end:
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                data += chunk
                last = time.monotonic()
                if until and until(data):
                    break
            elif quiet and data and time.monotonic() - last > quiet:
                break
        return data

    def test_first_start_lands_at_ready_prompt(self):
        import fcntl
        import pty
        import signal
        import struct
        import subprocess
        import termios
        self.assertFalse((self.claude_dir / ".claude.json").exists())
        self.assertFalse((self.home / ".claude.json").exists())
        env = dict(os.environ)
        env.update(self.env())
        env.update(TERM="xterm-256color", COLUMNS="120", LINES="40")
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        proc = subprocess.Popen([sys.executable, str(helpers.BIN / "dfir-lab"), "start"],
                                cwd=str(self.tmp), env=env, stdin=slave, stdout=slave,
                                stderr=slave, start_new_session=True)
        os.close(slave)
        try:
            # Wait until Claude Code has drawn its screen and gone quiet.
            first = self._read(master, None, 40, quiet=4)
            screen = _strip_ansi(first)
            for marker in self.THEME_SCREEN + self.TRUST_SCREEN:
                self.assertNotIn(marker, screen, "first-run screen shown:\n" + screen[-3000:])
            self.assertIn("Claude Code", screen, screen[-3000:])
            os.write(master, b"Say hello to the lab.\r")
            rest = self._read(master, lambda d: b"mock gateway" in d, 60)
            after = _strip_ansi(rest)
            self.assertIn("mock gateway", after,
                          "no reply after typing at the prompt:\n" + (screen + after)[-3000:])
            for marker in self.THEME_SCREEN + self.TRUST_SCREEN:
                self.assertNotIn(marker, after)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait(timeout=30)
            os.close(master)
        data = json.loads((self.claude_dir / ".claude.json").read_text())
        self.assertTrue(data["projects"][str(self.case)]["hasTrustDialogAccepted"])


if __name__ == "__main__":
    unittest.main()
