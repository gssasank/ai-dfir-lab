#!/usr/bin/env python3
"""The subscription route in the guest, as the student, up to the point where
a person signs in. Nothing here signs in or reads Claude Code's sign-in file.

    sub-route-pty.py use OUTDIR      `dfir-lab use subscription` in a pseudo-terminal
                                     with the desktop session's display: our lines,
                                     Claude Code's own sign-in step, Chromium opened
                                     with the sign-in page. Then stopped.
    sub-route-pty.py start OUTDIR    `dfir-lab start` in a pseudo-terminal, not signed
                                     in: it offers the sign-in, answered y, reaches
                                     Claude Code's sign-in step (not an error, the Duke
                                     gateway, the theme picker or the trust question).
    sub-route-pty.py claude OUTDIR   the program `dfir-lab start` runs: interactive
                                     Claude Code in the case folder with the
                                     subscription environment: no first-run screen,
                                     and a message gets "Not logged in".

Writes OUTDIR/<mode>-screen.txt (ANSI codes stripped) and OUTDIR/<mode>-result.json;
exit 0 when every check held.
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

THEME = ("Choose the text style", "Let's get started",
         # Claude Code 2.1.322's auto-mode nudge and banner (Enter would rewrite
         # Protocol SIFT's defaultMode); the subscription route suppresses both.
         "Make auto mode your default", "Auto mode is now Claude Code's default")
TRUST = ("trust this folder", "Quick safety check", "Accessing workspace", "Do you trust the files")
LOGIN_MENU = ("Select login method",)
GATEWAY = ("gateway", "Duke AI", "VPN")


def strip_ansi(data):
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", " ", text)
    text = re.sub(r"\x1b[@-_]", "", text)
    return re.sub(r"[ \t]+", " ", text)


def redact(text):
    return re.sub(r"sk-ant-[a-z]{2,8}[0-9]{0,3}-[A-Za-z0-9_\-]{8,}", "[REDACTED-CREDENTIAL]", text)


def desktop_env():
    """The environment a GNOME Terminal in the student's session has. The
    display variables, XAUTHORITY included (Chromium needs it for Xwayland),
    come from the session's systemd user manager."""
    uid = os.getuid()
    env = dict(os.environ)
    run = "/run/user/%d" % uid
    env.update(XDG_RUNTIME_DIR=run, DBUS_SESSION_BUS_ADDRESS="unix:path=%s/bus" % run,
               WAYLAND_DISPLAY="wayland-0", DISPLAY=":0", TERM="xterm-256color",
               COLUMNS="120", LINES="40",
               XDG_DATA_DIRS="/usr/share/ubuntu:/usr/share/gnome:/usr/local/share/:/usr/share/:"
                             "/var/lib/snapd/desktop")
    p = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                       text=True, env=env)
    for line in p.stdout.splitlines():
        name, _, value = line.partition("=")
        if name in ("XAUTHORITY", "DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
            env[name] = value
    env["PATH"] = "%s/.local/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin" % Path.home()
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop(k, None)
    return env


def spawn(argv, env, cwd):
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=slave, stdout=slave, stderr=slave,
                            start_new_session=True)
    os.close(slave)
    return proc, master


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


def stop(proc, master):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass
    os.close(master)


def chromium_signin_processes():
    p = subprocess.run(["pgrep", "-af", "chrom"], capture_output=True, text=True)
    return [l for l in p.stdout.splitlines() if "oauth/authorize" in l]


def close_chromium():
    subprocess.run(["pkill", "-f", "oauth/authorize"], capture_output=True)
    time.sleep(2)
    subprocess.run(["pkill", "-f", "/snap/chromium/"], capture_output=True)


def mode_use(out):
    checks = {}
    env = desktop_env()
    close_chromium()
    proc, master = spawn(["dfir-lab", "use", "subscription"], env, Path.home())
    try:
        raw = read_until(master, lambda d: b"Paste code here" in d, 120)
        time.sleep(8)               # Claude Code opens Chromium next to printing the link
        raw += read_until(master, None, 2, quiet=1)
        found = chromium_signin_processes()
    finally:
        stop(proc, master)
    screen = strip_ansi(raw)
    checks["explains_the_route"] = ("needs no Duke key and no VPN" in screen)
    checks["explains_the_sign_in"] = ("opens a Claude sign-in page" in screen
                                      and "copy the link printed below" in screen)
    checks["names_chromium"] = "Chromium opens a Claude sign-in page" in screen
    checks["claude_code_sign_in_started"] = "Opening browser to sign in" in screen
    checks["link_printed"] = "If the browser didn't open, visit: https://claude.com/cai/oauth/" \
                             "authorize" in screen
    checks["paste_prompt"] = "Paste code here if prompted" in screen
    checks["chromium_opened_sign_in_page"] = bool(found)
    checks["no_theme_or_trust_screen"] = not any(m in screen for m in THEME + TRUST)
    close_chromium()
    return checks, screen, {"chromium_processes": [redact(l)[:300] for l in found]}


def mode_start(out):
    checks = {}
    env = desktop_env()
    close_chromium()
    # --no-gui: with a desktop, dfir-lab asks its yes/no questions in a dialog
    # (zenity), as on the Duke route; the terminal form is what this script answers.
    proc, master = spawn(["dfir-lab", "start", "--no-gui"], env, Path.home())
    try:
        first = read_until(master, lambda d: b"[Y/n]" in d, 120)
        os.write(master, b"y\r")
        rest = read_until(master, lambda d: b"Paste code here" in d, 120)
        time.sleep(5)
        found = chromium_signin_processes()
    finally:
        stop(proc, master)
    s1, s2 = strip_ansi(first), strip_ansi(rest)
    screen = s1 + "\n----- after answering y -----\n" + s2
    checks["says_not_signed_in"] = "not signed in with your Claude subscription yet" in s1
    checks["offers_sign_in"] = "Sign in to your Claude account now? [Y/n]" in s1
    checks["reaches_claude_code_sign_in"] = ("Opening browser to sign in" in s2
                                             and "Paste code here if prompted" in s2)
    checks["no_gateway_or_vpn_talk"] = not any(m.lower() in screen.lower() for m in GATEWAY)
    checks["no_theme_trust_or_login_menu"] = not any(m in screen for m in THEME + TRUST
                                                     + LOGIN_MENU)
    checks["no_error"] = "Something unexpected" not in screen and "Traceback" not in screen
    checks["chromium_opened_sign_in_page"] = bool(found)
    close_chromium()
    return checks, screen, {}


def mode_claude(out):
    """Interactive Claude Code exactly as `dfir-lab start` would exec it."""
    sys.path.insert(0, "/usr/local/lib/dfir-lab")
    from dfir_lab import configure_claude
    from dfir_lab.claude_env import process_env
    from dfir_lab.config import Config
    cfg = Config()
    configure_claude.ensure(cfg, quiet=True, route="subscription")
    configure_claude.prepare_first_run(cfg, route="subscription")
    env = process_env(cfg, base=desktop_env(), route="subscription")
    checks = {"no_gateway_in_env": "ANTHROPIC_BASE_URL" not in env and "IS_DEMO" not in env,
              "model_is_sonnet": env.get("ANTHROPIC_MODEL") == "sonnet"}
    settings_path = Path.home() / ".claude" / "settings.json"
    mode_before = json.loads(settings_path.read_text()).get("permissions", {}).get("defaultMode")
    proc, master = spawn([cfg.claude_binary], env, cfg.case_dir)
    try:
        first = read_until(master, None, 60, quiet=5)
        os.write(master, b"hello\r")
        rest = read_until(master, lambda d: b"/login" in d, 60)
    finally:
        stop(proc, master)
    s1, s2 = strip_ansi(first), strip_ansi(rest)
    screen = s1 + "\n----- after typing hello -----\n" + s2
    checks["claude_code_drawn"] = "Claude Code" in s1
    checks["sonnet_shown"] = "Sonnet" in s1
    checks["no_theme_or_trust_screen"] = not any(m in screen for m in THEME + TRUST + LOGIN_MENU)
    checks["says_not_logged_in"] = "Not logged in" in s2
    mode_after = json.loads(settings_path.read_text()).get("permissions", {}).get("defaultMode")
    checks["permission_mode_unchanged"] = mode_after == mode_before
    return checks, screen, {"default_mode": [mode_before, mode_after]}


def main():
    mode, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    fn = {"use": mode_use, "start": mode_start, "claude": mode_claude}[mode]
    checks, screen, extra = fn(out)
    result = {"mode": mode, "checks": checks, "ok": all(checks.values())}
    result.update(extra)
    (out / ("%s-screen.txt" % mode)).write_text(redact(screen))
    (out / ("%s-result.json" % mode)).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
