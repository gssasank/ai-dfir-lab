"""The Claude Code environment from sections 6.1 and 6.4 of the spec.

Names verified against code.claude.com/docs (env-vars, settings,
authentication) on 2026-11-13: ANTHROPIC_BASE_URL, ANTHROPIC_MODEL,
ANTHROPIC_DEFAULT_OPUS_MODEL, ANTHROPIC_DEFAULT_SONNET_MODEL,
ANTHROPIC_DEFAULT_HAIKU_MODEL, CLAUDE_CODE_SUBAGENT_MODEL,
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC, DISABLE_AUTOUPDATER, IS_DEMO
("skips onboarding"), CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL
("Set to 1 to disable automatic registration of the official plugin
marketplace ... usually during a machine's first interactive launch";
CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC does not cover it), CLAUDE_CODE_EFFORT_LEVEL
and the CLAUDE_CODE_DISABLE_* names in LOAD_REDUCTION_ENV (checked 2026-11-14),
ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN and the
provider selectors CLAUDE_CODE_USE_BEDROCK / _VERTEX / _FOUNDRY /
_ANTHROPIC_AWS / _MANTLE. The key is never placed in the environment;
Claude Code gets it from the apiKeyHelper setting.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from . import route as route_mod
from .errors import ConfigError

# Variables that would make Claude Code use some other credential or some
# other provider instead of the stored key through the helper (the
# authentication precedence in code.claude.com/docs/en/authentication puts
# all of these above apiKeyHelper, and above a subscription sign-in).
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
PROVIDER_VARS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                 "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_MANTLE")
CONFLICTING_VARS = CREDENTIAL_VARS + PROVIDER_VARS

# Set only on the Duke route (the gateway lock and its model and effort
# choices). The subscription route removes them from the settings file and
# from Claude Code's environment; IS_DEMO goes too, because it also hides the
# account in Claude Code's header and /status (code.claude.com/docs/en/env-vars).
DUKE_ONLY_VARS = ("ANTHROPIC_BASE_URL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                  "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                  "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL", "IS_DEMO")

# Chromium is the VM's working browser (Firefox does not work there). Claude
# Code 2.1.322 opens the sign-in page with $BROWSER, else xdg-open (observed in
# the binary; BROWSER is not in the env-vars docs).
CHROMIUM_CANDIDATES = ("chromium", "/snap/bin/chromium", "chromium-browser")

# Load reduction (measured against the Duke gateway on 2026-11-14, see
# research/2026-11-14-gateway-proof.md). The gateway counts every token of
# every request, cached or not, against a per-key tokens-per-minute limit, so
# whatever Claude Code sends with each request matters. These remove features
# the lab never uses; names checked in code.claude.com/docs/en/env-vars on
# 2026-11-14. The matching tool names are denied in the settings fragment.
LOAD_REDUCTION_ENV = {
    "CLAUDE_CODE_DISABLE_WORKFLOWS": "1",       # Workflow tool (~2,300 tokens per request)
    "CLAUDE_CODE_DISABLE_CRON": "1",            # /loop and scheduled tasks
    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",  # coding skills listed in every request
    "CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1",
    "CLAUDE_CODE_DISABLE_ARTIFACT": "1",        # publishes to claude.ai; unusable with a gateway key
    "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",  # skips the background session-title request
}

# The guard hook's onFailure: "block" needs this version
# (code.claude.com/docs/en/hooks: "Requires Claude Code v2.1.295 or later").
MIN_CLAUDE_VERSION = (2, 1, 295)


def _common_env():
    """Settings both routes share (the lab's tool restrictions included)."""
    env = {
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        # No clone of the official plugin marketplace from GitHub on the first
        # interactive start (code.claude.com/docs/en/env-vars, checked 2026-11-13).
        "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
    }
    env.update(LOAD_REDUCTION_ENV)
    return env


def settings_env(cfg, route=None):
    """The `env` block we merge into ~/.claude/settings.json for a route
    (default: the stored route).

    Duke route: the conflicting variables are set to an empty string there. A
    settings file cannot remove a variable that Claude Code inherited, but the
    docs say an empty value counts as unset for provider selection, and with
    Claude Code 2.1.322 an empty ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN in
    settings also made it fall back to the helper (observed, not documented).

    Subscription route: no gateway, no helper, no credential variables at all,
    no IS_DEMO, no forced effort; only the model (the current Sonnet by
    default, which the student may change with /model) and the shared lab
    settings. [claude] extra_env is gateway tuning and is not applied."""
    route = route_mod.resolve(route)
    if route == route_mod.SUBSCRIPTION:
        env = _common_env()
        env["ANTHROPIC_MODEL"] = cfg.subscription_model
        return env
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
    }
    env.update(_common_env())
    # First interactive start (spec 6.4): skip the onboarding/theme screen.
    env["IS_DEMO"] = "1"
    effort = cfg.effort
    if effort:
        # Takes precedence over --effort, /effort and the effortLevel setting
        # (code.claude.com/docs/en/model-config, "Set the effort level").
        env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
    env.update(extra)
    for name in CONFLICTING_VARS:
        env[name] = ""
    return env


def removed_settings_vars(route=None):
    """Names the settings merge deletes from `env` for a route: on the
    subscription route the whole gateway lock (and its empty credential
    placeholders, so no API-key variable is present at all)."""
    if route_mod.resolve(route) == route_mod.SUBSCRIPTION:
        return DUKE_ONLY_VARS + CONFLICTING_VARS
    return ()


def chromium():
    """Path of the VM's Chromium, or None."""
    for c in CHROMIUM_CANDIDATES:
        p = c if os.sep in c else shutil.which(c)
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def browser_env(env):
    """{"BROWSER": chromium} when a desktop is available, Chromium is
    installed and the student has not chosen a browser; otherwise {}."""
    if env.get("BROWSER") or not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return {}
    path = chromium()
    return {"BROWSER": path} if path else {}


def process_env(cfg, base=None, route=None):
    """Environment for a Claude Code process we start (start, check 10).
    Same values as the settings, so Claude Code is configured correctly
    even if something later overwrote ~/.claude/settings.json; the
    conflicting variables are removed outright. On the subscription route the
    gateway variables are removed as well, and the sign-in opens Chromium."""
    route = route_mod.resolve(route)
    env = dict(os.environ if base is None else base)
    if route == route_mod.SUBSCRIPTION:
        for name in DUKE_ONLY_VARS + CONFLICTING_VARS:
            env.pop(name, None)
        env.update(settings_env(cfg, route))
        env.update(browser_env(env))
        return env
    env.update(settings_env(cfg, route))
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
    version, line = claude_version(exe, process_env(cfg, route=route_mod.DUKE))
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
