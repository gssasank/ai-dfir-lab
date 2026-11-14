#!/usr/bin/env python3
"""First interactive start, in the guest, as the student.

Adapted from lab/tests/test_e2e_claude.py (FirstInteractiveStartTests). Runs
the installed `dfir-lab start` in a pseudo-terminal with the student's real
home folder and settings, waits for Claude Code to draw its screen, checks
that no first-run screen is showing (theme picker, workspace trust, login,
API-key approval), types a prompt and waits for the reply.

    first-start-pty.py OUTDIR PROMPT EXPECTED_REPLY_TEXT

Writes OUTDIR/pty-screen.txt (ANSI codes stripped) and OUTDIR/pty-result.json
and exits 0 when every assertion held, 1 otherwise. Never prints the key
(dfir-lab and Claude Code do not show it; the screen is also checked).
"""

import fcntl
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

THEME_SCREEN = ("Choose the text style", "Let's get started")
TRUST_SCREEN = ("trust this folder", "Quick safety check", "Accessing workspace",
                "Do you trust the files")
LOGIN_SCREEN = ("Select login method", "Claude account with subscription",
                "Anthropic Console account", "Log in with your")
KEY_SCREEN = ("Do you want to use this API key", "Detected a custom API key")
FORBIDDEN = THEME_SCREEN + TRUST_SCREEN + LOGIN_SCREEN + KEY_SCREEN


def strip_ansi(data):
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", text)      # OSC
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", " ", text)             # CSI
    text = re.sub(r"\x1b[@-_]", "", text)
    return re.sub(r"[ \t]+", " ", text)


def read_until(fd, until, timeout, quiet=None):
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


def main():
    out = Path(sys.argv[1])
    prompt, expected = sys.argv[2], sys.argv[3]
    out.mkdir(parents=True, exist_ok=True)
    result = {"ok": False, "checks": {}}
    env = dict(os.environ)
    env.update(TERM="xterm-256color", COLUMNS="120", LINES="40")
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(k, None)
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    proc = subprocess.Popen(["dfir-lab", "start"], cwd=str(Path.home()), env=env,
                            stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    screen = after = ""
    try:
        first = read_until(master, None, 60, quiet=5)
        screen = strip_ansi(first)
        shown = [m for m in FORBIDDEN if m in screen]
        result["checks"]["no_first_run_screen"] = not shown
        result["first_run_markers_seen"] = shown
        result["checks"]["claude_code_drawn"] = "Claude Code" in screen
        result["checks"]["banner_shown"] = "DFIR Lab" in screen
        os.write(master, prompt.encode() + b"\r")
        rest = read_until(master, lambda d: expected.encode() in d, 120)
        after = strip_ansi(rest)
        result["checks"]["reply_received"] = expected in after
        shown2 = [m for m in FORBIDDEN if m in after]
        result["checks"]["no_first_run_screen_after_prompt"] = not shown2
        # Let the Stop hook (Protocol SIFT's) run after the reply.
        time.sleep(4)
        tail = strip_ansi(read_until(master, None, 3, quiet=2))
        after += tail
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
        os.close(master)
    keyfile = Path.home() / ".config" / "dfir-lab" / "duke-api-key"
    key = keyfile.read_text().strip() if keyfile.is_file() else ""
    full = screen + "\n----- after typing the prompt -----\n" + after
    result["checks"]["key_not_on_screen"] = not key or key not in full
    if key:
        full = full.replace(key, "[REDACTED-KEY]")
    (out / "pty-screen.txt").write_text(full)
    result["ok"] = all(result["checks"].values())
    (out / "pty-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
