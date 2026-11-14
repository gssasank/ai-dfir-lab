"""Screen output and questions.

All output goes through `out()` / `err()`, which call the redaction
function. Questions use zenity when a display is available (and --no-gui
was not given) and fall back to the terminal.
"""

import getpass
import os
import shutil
import subprocess
import sys

from .errors import LabError
from .redact import redact

_state = {"no_gui": False, "debug": False}


def configure(no_gui=False, debug=False):
    _state["no_gui"] = bool(no_gui)
    _state["debug"] = bool(debug)


def debug_enabled():
    return _state["debug"]


def out(text="", end="\n", stream=None):
    stream = stream or sys.stdout
    try:
        stream.write(redact(text) + end)
        stream.flush()
    except BrokenPipeError:  # e.g. piped into `head`
        pass


def err(text="", end="\n"):
    out(text, end=end, stream=sys.stderr)


def debug(text):
    if _state["debug"]:
        err("[debug] " + str(text))


def problem(what, todo=None, stream=None):
    """Print a failure message: what happened, then what to do."""
    out("Problem: " + what, stream=stream or sys.stderr)
    if todo:
        out("What to do: " + todo, stream=stream or sys.stderr)


def has_gui():
    if _state["no_gui"]:
        return False
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return shutil.which("zenity") is not None


def interactive():
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _no_terminal():
    return LabError(
        "This step needs an answer from you, but there is no terminal to type it into.",
        "Open a terminal window and run the command there.",
    )


def _zenity(args):
    try:
        proc = subprocess.run(
            ["zenity"] + args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=3600,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    return proc.returncode, (proc.stdout or "").rstrip("\n")


def ask_secret(prompt, title="DFIR Lab"):
    """Hidden input. Returns the typed text, or None if cancelled."""
    if has_gui():
        code, value = _zenity(["--entry", "--hide-text", "--title", title, "--text", prompt,
                               "--width", "480"])
        if code is not None:
            return value if code == 0 else None
    if not interactive():
        raise _no_terminal()
    try:
        return getpass.getpass(prompt + " ")
    except (EOFError, KeyboardInterrupt):
        out("")
        return None


def ask_text(prompt, title="DFIR Lab", default=""):
    if has_gui():
        args = ["--entry", "--title", title, "--text", prompt, "--width", "480"]
        if default:
            args += ["--entry-text", default]
        code, value = _zenity(args)
        if code is not None:
            return value if code == 0 else None
    if not interactive():
        raise _no_terminal()
    try:
        suffix = (" [%s]" % default) if default else ""
        out(prompt + suffix + " ", end="")
        value = input()
    except (EOFError, KeyboardInterrupt):
        out("")
        return None
    return value.strip() or default


def ask_yes_no(question, default=True, title="DFIR Lab"):
    if has_gui():
        code, _ = _zenity(["--question", "--title", title, "--text", redact(question),
                           "--width", "480"])
        if code is not None:
            return code == 0
    if not interactive():
        raise _no_terminal()
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        out("%s %s " % (question, hint), end="")
        try:
            ans = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            out("")
            return False
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        out("Please type y or n.")


def ask_choice(question, choices, title="DFIR Lab"):
    """choices: list of (key, label). Returns a key, or None if cancelled."""
    if has_gui():
        args = ["--list", "--title", title, "--text", redact(question), "--column", "Choice",
                "--width", "520", "--height", "300"]
        args += [label for _, label in choices]
        code, value = _zenity(args)
        if code is not None:
            if code != 0:
                return None
            for key, label in choices:
                if label == value:
                    return key
            return None
    if not interactive():
        raise _no_terminal()
    out(question)
    for i, (_, label) in enumerate(choices, 1):
        out("  %d) %s" % (i, label))
    while True:
        out("Type a number and press Enter: ", end="")
        try:
            ans = input().strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            return None
        if ans.isdigit() and 1 <= int(ans) <= len(choices):
            return choices[int(ans) - 1][0]
        out("Please type a number from 1 to %d." % len(choices))


def notify(title, text, error=False):
    """Show a dialog when a GUI is available (text is also printed by callers)."""
    if has_gui():
        _zenity(["--error" if error else "--info", "--title", title, "--text", redact(text),
                 "--width", "520"])


def pause():
    if not interactive():
        return
    out("")
    out("Press Enter to close this window.", end="")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def table(rows, headers):
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(str(cell)))
    def fmt(r):
        return "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip()
    lines = [fmt(headers), fmt(["-" * w for w in widths])]
    lines += [fmt(r) for r in rows]
    return "\n".join(lines)
