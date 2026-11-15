"""Read /etc/dfir-lab/lab.conf (or $DFIR_LAB_CONF).

Every value has a default here or is required. Required values are checked
when a command needs them, so `dfir-lab setup-key --status` works even
before a case is configured.
"""

import configparser
import re
from pathlib import Path

from . import paths
from .errors import ConfigError

DEFAULTS = {
    "lab": {
        "version": "0.1.0",
        "course": "CYBERSEC 510",
        "support_contact": "",
    },
    "gateway": {
        "base_url": "",
        "auth_style": "bearer",
        "model_main": "",
        "model_small": "",
        "connect_timeout_s": "8",
        "request_timeout_s": "60",
        "network_hint": "Connect to the Duke VPN on your host computer, then try again.",
        "dashboard_url": "",
    },
    "claude": {
        "binary": "claude",
        "max_turns_readiness": "6",
        "readiness_timeout_s": "180",
        # Claude Code effort level (CLAUDE_CODE_EFFORT_LEVEL); empty = Claude Code's default.
        "effort": "",
        "extra_env": "",
    },
    "case": {
        "id": "",
        "title": "",
        "dir": "",
        "manifest": "MANIFEST.sha256",
        "checkpoints": "checkpoints.json",
        "tools_file": "tools.list",
        "replay_dir": "fallback",
        # Off when unset: students fill in the worksheet outside the VM.
        "worksheet": "",
        "worksheet_template": "",
        "ticket": "ticket.md",
        # The AI-written report students audit (named in the start banner).
        "ai_report": "",
    },
    # Optional route: the student's own Claude subscription instead of the gateway
    # (dfir-lab use subscription). Nothing here is needed for the Duke route.
    "subscription": {
        # Claude Code model alias or ID for this route; "sonnet" is the current Sonnet.
        "model": "sonnet",
        # Hosts readiness check 5 connects to on this route: the API, the sign-in
        # token service and the sign-in pages (seen in Claude Code 2.1.322).
        "hosts": "https://api.anthropic.com https://platform.claude.com https://claude.ai",
    },
    # The case's disk image (dfir-lab mount). An empty image means the case has none.
    "disk": {
        "image": "",
        "segments": "",
        "mount_point": "/mnt/dc01",
        "ewf_mount": "",
        "probe": "Windows/System32/config/SYSTEM",
        "probe_magic": "regf",
        "zip_name": "",
    },
    "requirements": {
        "min_ram_mb": "5500",
        "min_free_disk_gb": "10",
        "min_cpus": "2",
    },
}

REQUIRED = {
    ("gateway", "base_url"),
    ("gateway", "model_main"),
    ("case", "id"),
    ("case", "dir"),
}

AUTH_STYLES = ("bearer", "x-api-key", "both")
# Values CLAUDE_CODE_EFFORT_LEVEL accepts (code.claude.com/docs/en/env-vars, read 2026-11-14).
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max", "auto")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Config:
    def __init__(self, path=None):
        self.path = Path(path) if path else paths.conf_path()
        self._cp = configparser.ConfigParser(
            interpolation=None, inline_comment_prefixes=(";",), strict=False
        )
        self._cp.read_dict(DEFAULTS)
        if not self.path.is_file():
            raise ConfigError(
                "The lab settings file %s is missing." % self.path,
                "The lab toolkit is not installed completely. Tell the TA; "
                "this is not something you broke.",
            )
        try:
            with open(self.path, encoding="utf-8") as fh:
                self._cp.read_file(fh)
        except (configparser.Error, UnicodeDecodeError) as exc:
            raise ConfigError(
                "The lab settings file %s could not be read (%s)."
                % (self.path, exc.__class__.__name__),
                "Tell the TA; the settings file needs to be fixed.",
            )

    # -- raw access -----------------------------------------------------
    def get(self, section, key):
        try:
            return self._cp.get(section, key).strip()
        except (configparser.NoSectionError, configparser.NoOptionError):
            return DEFAULTS.get(section, {}).get(key, "")

    def require(self, section, key):
        value = self.get(section, key)
        if not value:
            raise ConfigError(
                "The setting [%s] %s is missing from %s." % (section, key, self.path),
                "The lab is not configured completely. Tell the TA which "
                "setting is missing.",
            )
        return value

    def require_all(self, pairs=None):
        for section, key in sorted(pairs or REQUIRED):
            self.require(section, key)

    def get_int(self, section, key):
        raw = self.get(section, key)
        try:
            return int(raw)
        except ValueError:
            raise ConfigError(
                "The setting [%s] %s in %s must be a whole number, not %r."
                % (section, key, self.path, raw),
                "Tell the TA; the settings file needs to be fixed.",
            )

    def get_float(self, section, key):
        raw = self.get(section, key)
        try:
            return float(raw)
        except ValueError:
            raise ConfigError(
                "The setting [%s] %s in %s must be a number, not %r."
                % (section, key, self.path, raw),
                "Tell the TA; the settings file needs to be fixed.",
            )

    # -- lab ------------------------------------------------------------
    @property
    def lab_version(self):
        return self.get("lab", "version") or "0.0.0"

    @property
    def course(self):
        return self.get("lab", "course")

    @property
    def support_contact(self):
        return self.get("lab", "support_contact")

    def contact_line(self):
        c = self.support_contact
        return ("contact %s" % c) if c else "tell the TA"

    # -- gateway --------------------------------------------------------
    @property
    def base_url(self):
        url = self.require("gateway", "base_url").rstrip("/")
        # The setting is the root URL; tolerate a trailing /v1 so Claude Code
        # does not end up calling /v1/v1/messages.
        if url.endswith("/v1"):
            url = url[: -len("/v1")]
        if not re.match(r"^https?://[^/\s]+", url):
            raise ConfigError(
                "The setting [gateway] base_url in %s is not a web address: %r."
                % (self.path, url),
                "Tell the TA; it must look like https://host.example.edu",
            )
        return url

    @property
    def auth_style(self):
        style = self.get("gateway", "auth_style").lower()
        if style not in AUTH_STYLES:
            raise ConfigError(
                "The setting [gateway] auth_style in %s must be one of %s, not %r."
                % (self.path, ", ".join(AUTH_STYLES), style),
                "Tell the TA; the settings file needs to be fixed.",
            )
        return style

    @property
    def model_main(self):
        return self.require("gateway", "model_main")

    @property
    def model_small(self):
        return self.get("gateway", "model_small") or self.model_main

    @property
    def connect_timeout(self):
        return self.get_float("gateway", "connect_timeout_s")

    @property
    def request_timeout(self):
        return self.get_float("gateway", "request_timeout_s")

    @property
    def network_hint(self):
        return self.get("gateway", "network_hint")

    @property
    def dashboard_url(self):
        return self.get("gateway", "dashboard_url")

    # -- claude ---------------------------------------------------------
    @property
    def claude_binary(self):
        return self.get("claude", "binary") or "claude"

    @property
    def effort(self):
        value = self.get("claude", "effort").lower()
        if value and value not in EFFORT_LEVELS:
            raise ConfigError(
                "The setting [claude] effort in %s must be one of %s (or empty), not %r."
                % (self.path, ", ".join(EFFORT_LEVELS), value),
                "Tell the TA; the settings file needs to be fixed.")
        return value

    @property
    def extra_env(self):
        out = {}
        for raw in self.get("claude", "extra_env").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition("=")
            name = name.strip()
            if not sep or not _ENV_NAME.match(name):
                raise ConfigError(
                    "The setting [claude] extra_env in %s has a line that is not "
                    "NAME=VALUE: %r." % (self.path, line),
                    "Tell the TA; the settings file needs to be fixed.",
                )
            out[name] = value.strip()
        return out

    # -- subscription route ---------------------------------------------
    @property
    def subscription_model(self):
        value = self.get("subscription", "model") or "sonnet"
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._\[\]-]*$", value):
            raise ConfigError(
                "The setting [subscription] model in %s is not a model name: %r."
                % (self.path, value),
                "Tell the TA; the settings file needs to be fixed.")
        return value

    @property
    def subscription_hosts(self):
        hosts = self.get("subscription", "hosts").split()
        # https:// in lab.conf; http:// is accepted for local tests.
        bad = [h for h in hosts if not re.match(r"^https?://[^/\s]+$", h)]
        if bad or not hosts:
            raise ConfigError(
                "The setting [subscription] hosts in %s must list web addresses, not %r."
                % (self.path, " ".join(bad or hosts)),
                "Tell the TA; the settings file needs to be fixed.")
        return hosts

    # -- case -----------------------------------------------------------
    @property
    def case_id(self):
        return self.require("case", "id")

    @property
    def case_title(self):
        return self.get("case", "title") or self.case_id

    @property
    def case_dir(self):
        d = Path(self.require("case", "dir")).expanduser()
        if not d.is_absolute():
            # Relative paths are relative to the settings file (handy for
            # development against the sample case kit).
            d = (self.path.parent / d).resolve()
        return d

    def case_path(self, key):
        value = self.get("case", key)
        if not value:
            return None
        p = Path(value).expanduser()
        return p if p.is_absolute() else self.case_dir / p
