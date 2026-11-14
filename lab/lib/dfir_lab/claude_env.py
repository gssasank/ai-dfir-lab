"""The Claude Code environment from sections 6.1 and 6.4 of the spec.

Names verified against code.claude.com/docs (env-vars, settings,
authentication) on 2026-11-13: ANTHROPIC_BASE_URL, ANTHROPIC_MODEL,
ANTHROPIC_DEFAULT_OPUS_MODEL, ANTHROPIC_DEFAULT_SONNET_MODEL,
ANTHROPIC_DEFAULT_HAIKU_MODEL, CLAUDE_CODE_SUBAGENT_MODEL,
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC, DISABLE_AUTOUPDATER, IS_DEMO
("skips onboarding"), CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL
("Set to 1 to disable automatic registration of the official plugin
marketplace ... usually during a machine's first interactive launch";
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC does not cover it), ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN and the
provider selectors CLAUDE_CODE_USE_BEDROCK / _VERTEX / _FOUNDRY /
_ANTHROPIC_AWS / _MANTLE. The key is never placed in the environment;
Claude Code gets it from the apiKeyHelper setting.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from .errors import ConfigError

# Variables that would make Claude Code use some other credential or some
# other provider instead of the stored key through the helper (the
# authentication precedence in code.claude.com/docs/en/authentication puts
# all of these above apiKeyHelper).
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
PROVIDER_VARS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                 "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_MANTLE")
CONFLICTING_VARS = CREDENTIAL_VARS + PROVIDER_VARS

# The guard hook's onFailure: "block" needs this version
# (code.claude.com/docs/en/hooks: "Requires Claude Code v2.1.295 or later").
MIN_CLAUDE_VERSION = (2, 1, 295)


def settings_env(cfg):
    """The `env` block we merge into ~/.claude/settings.json.

    The conflicting variables are set to an empty string there. A settings
    file cannot remove a variable that Claude Code inherited, but the docs say
    an empty value counts as unset for provider selection, and with Claude
    Code 2.1.322 an empty ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in settings
    also made it fall back to the helper (observed, not documented)."""
    main, small = cfg.model_main, cfg.model_small
    extra = cfg.extra_env
    bad = sorted(set(extra) & set(CONFLICTING_VARS))
    if bad:
        raise ConfigError(
            "The setting [claude] extra_env in %s sets %s. That would make Claude Code use "
            "another credential or provider instead of the student's key." % (
                cfg.path, ", ".join(bad)),
            "Tell the TA; remove those names from extra_env.")
    env = {
        "ANTHROPIC_BASE_URL": cfg.base_url,
        "ANTHROPIC_MODEL": main,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": main,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": main,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": small,
        "CLAUDE_CODE_SUBAGENT_MODEL": main,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        # No clone of the official plugin marketplace from GitHub on the first
        # interactive start (code.claude.com/docs/en/env-vars, checked 2026-11-13).
        "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
        # First interactive start (spec 6.4): skip the onboarding/theme screen.
        "IS_DEMO": "1",
    }
    env.update(extra)
    for name in CONFLICTING_VARS:
        env[name] = ""
    return env


def process_env(cfg, base=None):
    """Environment for a Claude Code process we start (start, check 10).
    Same values as the settings, so Claude Code is configured correctly
    even if something later overwrote ~/.claude/settings.json; the
    conflicting variables are removed outright."""
    env = dict(os.environ if base is None else base)
    env.update(settings_env(cfg))
    for name in CONFLICTING_VARS:
        env.pop(name, None)
    return env


def find_claude(cfg):
    binary = cfg.claude_binary
    path = binary if os.sep in binary else shutil.which(binary)
    if not path or not Path(path).is_file() or not os.access(path, os.X_OK):
        raise ConfigError(
            "Claude Code is not installed (the program %r was not found)." % binary,
            "The VM is missing a part. Tell the TA.",
        )
    return path


def parse_version(text):
    """'2.1.322 (Claude Code)' -> (2, 1, 322), or None."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def claude_version(exe, env=None):
    """(version tuple or None, first line of `claude --version`)."""
    try:
        p = subprocess.run([exe, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=30, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "could not run %s --version (%s)" % (exe, exc.__class__.__name__)
    first = (p.stdout or "").strip().splitlines()
    line = first[0] if first else "no output (exit %d)" % p.returncode
    return parse_version(line), line


def require_min_version(cfg, exe):
    """Raise ConfigError unless Claude Code is new enough (spec 6.4)."""
    version, line = claude_version(exe, process_env(cfg))
    need = ".".join(map(str, MIN_CLAUDE_VERSION))
    if version is None:
        raise ConfigError(
            "Could not tell which version of Claude Code is installed (%s). The lab needs "
            "%s or later." % (line, need),
            "The VM's Claude Code installation is broken. Tell the TA.")
    if version < MIN_CLAUDE_VERSION:
        raise ConfigError(
            "Claude Code %s is installed, but the lab needs %s or later (older versions do not "
            "support a hook setting the lab relies on)." % (".".join(map(str, version)), need),
            "Run the lab setup script again to install the tested version, or tell the TA.")
    return version
