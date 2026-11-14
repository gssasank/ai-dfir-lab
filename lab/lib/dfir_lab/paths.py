"""Where things live. Everything per-student is derived from $HOME so tests
can point HOME at a temporary directory."""

import os
from pathlib import Path

DEFAULT_CONF = "/etc/dfir-lab/lab.conf"
KEY_FILE_NAME = "duke-api-key"
HELPER_NAME = "key-helper"
GUARD_NAME = "pretool-guard"


def home():
    return Path(os.path.expanduser("~"))


def key_dir():
    return home() / ".config" / "dfir-lab"


def key_file():
    return key_dir() / KEY_FILE_NAME


def state_dir():
    return home() / "dfir-lab"


def readiness_dir():
    return state_dir() / "readiness"


def logs_dir():
    return state_dir() / "logs"


def progress_file():
    return state_dir() / "progress.json"


def toolkit_log():
    return logs_dir() / "dfir-lab.log"


def sessions_log():
    return logs_dir() / "sessions.log"


def conf_path():
    return Path(os.environ.get("DFIR_LAB_CONF") or DEFAULT_CONF)


def claude_config_dir():
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else home() / ".claude"


def claude_settings_file():
    return claude_config_dir() / "settings.json"


def claude_global_config():
    """Claude Code's own state file (~/.claude.json). It moves into
    $CLAUDE_CONFIG_DIR when that is set (code.claude.com/docs/en/claude-directory;
    also observed with Claude Code 2.1.322)."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) / ".claude.json" if env else home() / ".claude.json"


def package_dir():
    return Path(__file__).resolve().parent


def _first_existing(candidates):
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def install_root():
    """Directory that holds dfir_lab/, libexec/ and share/.

    Repository layout:  lab/lib/dfir_lab  -> lab/libexec, lab/share
    Installed layout:   /usr/local/lib/dfir-lab/dfir_lab -> .../dfir-lab/libexec
    """
    pkg = package_dir()
    for root in (pkg.parent, pkg.parent.parent):
        if (root / "libexec").is_dir():
            return root
    return pkg.parent


def libexec_dir():
    env = os.environ.get("DFIR_LAB_LIBEXEC")
    if env:
        return Path(env)
    return install_root() / "libexec"


def share_dir():
    return install_root() / "share"
