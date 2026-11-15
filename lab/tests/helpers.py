"""Shared test fixtures: a temporary HOME, a copy of the sample case kit,
a lab.conf, and the mock gateway running in a thread.

All temporary files live under the system temp directory (/tmp on Linux),
never on a mounted Windows drive, so file modes behave like Linux modes.
"""

import json
import os
import random
import shutil
import socket
import stat
import string
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

LAB = Path(__file__).resolve().parents[1]
REPO = LAB.parent
FIXTURES = LAB / "tests" / "fixtures"
FIXTURE = FIXTURES / "sample-case"
MOCK_DIR = REPO / "tools" / "mock-gateway"
BIN = LAB / "bin"

for p in (LAB / "lib", MOCK_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import mock_gateway  # noqa: E402
from dfir_lab import redact  # noqa: E402

KEY_CHARS = string.ascii_letters + string.digits + "-_"
PRINTABLE_NOSPACE = "".join(c for c in string.printable if not c.isspace())


def random_key(rng=random, n=None, charset=KEY_CHARS):
    n = n or rng.randint(20, 60)
    return "sk-" + "".join(rng.choice(charset) for _ in range(n))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_writable(path):
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(root, name)
            try:
                if not os.path.islink(p):
                    os.chmod(p, os.stat(p).st_mode | stat.S_IWUSR | stat.S_IRUSR | stat.S_IXUSR
                             if os.path.isdir(p) else os.stat(p).st_mode | stat.S_IWUSR)
            except OSError:
                pass
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


FAKE_CLAUDE = r'''#!/bin/sh
# Fake Claude Code used by the tests. Records argv/env/cwd, and in -p mode
# behaves like a model that runs the readiness script. `--version` is not
# logged, so FAKE_CLAUDE_LOG exists only once Claude Code was really started.
case " $* " in
  *" --version "*) echo "${FAKE_CLAUDE_VERSION:-9.9.9} (Fake Claude Code)"; exit 0 ;;
esac
# `claude auth status|login`: the subscription sign-in, faked. A fake sign-in
# file in the config directory means "signed in". These calls are logged to
# FAKE_CLAUDE_AUTH_LOG, not FAKE_CLAUDE_LOG.
cred="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.credentials.json"
if [ "$1" = "auth" ]; then
  alog="${FAKE_CLAUDE_AUTH_LOG:-/dev/null}"
  { echo "AUTH=$*"; env | sed 's/^/ENV=/'; } >> "$alog"
  case "$2" in
    status)
      if [ -f "$cred" ]; then
        printf '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty", "email": "student@example.edu", "orgName": "Example", "subscriptionType": "%s", "loginExpiresAt": %s}\n' \
          "${FAKE_CLAUDE_PLAN:-pro}" "${FAKE_CLAUDE_EXPIRES:-null}"
        exit 0
      fi
      if [ -n "${FAKE_CLAUDE_OTHER_AUTH:-}" ]; then
        printf '{"loggedIn": true, "authMethod": "%s", "apiProvider": "firstParty", "loginExpiresAt": null}\n' "$FAKE_CLAUDE_OTHER_AUTH"
        exit 0
      fi
      echo '{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty", "loginExpiresAt": null}'
      exit 1 ;;
    login)
      url="https://claude.com/cai/oauth/authorize?code=true&client_id=fake&state=fake"
      echo "Opening browser to sign in…"
      if [ -n "${BROWSER:-}" ]; then "$BROWSER" "$url" >/dev/null 2>&1; fi
      echo "If the browser didn't open, visit: $url"
      printf 'Paste code here if prompted > '
      if [ "${FAKE_CLAUDE_LOGIN:-ok}" = "ok" ]; then
        mkdir -p "$(dirname "$cred")"
        umask 077
        printf '{"claudeAiOauth": {"accessToken": "%s", "refreshToken": "%s", "subscriptionType": "pro"}}\n' \
          "${FAKE_CLAUDE_TOKEN:-sk-ant-oat01-faketokenfaketokenfaketoken}" "sk-ant-ort01-fakerefreshfakerefreshfake" > "$cred"
        echo; echo "Login successful."
        exit 0
      fi
      echo; echo "Login failed: That code wasn't accepted." >&2
      exit 1 ;;
  esac
  exit 2
fi
out="${FAKE_CLAUDE_LOG:-/dev/null}"
{
  echo "CWD=$(pwd)"
  for a in "$@"; do echo "ARG=$a"; done
  env | sed 's/^/ENV=/'
} > "$out"
if [ "$1" = "-p" ]; then
  if [ "${FAKE_CLAUDE_MODE:-ok}" = "ok" ]; then
    sh readiness.sh >/dev/null
    echo '{"type":"result","subtype":"success","is_error":false,"num_turns":2,"result":"DONE","usage":{"input_tokens":120,"output_tokens":8}}'
    exit 0
  fi
  # Results Claude Code 2.1.322 printed (or, for limit, documents) on the subscription route.
  case "$FAKE_CLAUDE_MODE" in
    notloggedin) r='Not logged in · Please run /login' ;;
    expired) r='Failed to authenticate. API Error: 401 OAuth access token is invalid.' ;;
    limit) r="You've hit your session limit · resets 3:45pm" ;;
    offline) r='API Error: Connection refused — a firewall or proxy may be blocking it (ECONNREFUSED)' ;;
    *) r='' ;;
  esac
  if [ -n "$r" ]; then
    printf '{"type":"result","subtype":"success","is_error":true,"num_turns":1,"result":"%s","usage":{"input_tokens":0,"output_tokens":0}}\n' "$r"
    exit 1
  fi
  echo '{"type":"result","subtype":"error_max_turns","is_error":true,"num_turns":6,"result":"","usage":{"input_tokens":50,"output_tokens":5}}'
  exit 1
fi
exit 0
'''


class LabTestCase(unittest.TestCase):
    """Temporary HOME + sample case + lab.conf + mock gateway."""

    start_mock = True
    fault = "none"
    request_timeout = 5
    claude_binary = None   # None -> fake claude script

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dfir-test-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.case = self.tmp / "case"
        shutil.copytree(FIXTURE, self.case)
        for sub in ("analysis", "reports", "exports"):
            shutil.rmtree(self.case / sub, ignore_errors=True)
        for f in ("worksheet.md",):
            try:
                (self.case / f).unlink()
            except FileNotFoundError:
                pass
        ev = self.case / "evidence"
        for f in ev.iterdir():
            os.chmod(f, 0o444)
        os.chmod(ev, 0o555)
        self.claude_dir = self.tmp / "claude-config"
        self.fake_claude = self.tmp / "fake-claude"
        self.fake_claude.write_text(FAKE_CLAUDE)
        os.chmod(self.fake_claude, 0o755)
        self.fake_log = self.tmp / "fake-claude.log"
        self.fake_auth_log = self.tmp / "fake-claude-auth.log"
        self.mock_key = random_key()
        self.mock_log = self.tmp / "mock.log"
        self.server = None
        if self.start_mock:
            self.server, self.base_url = mock_gateway.start_in_thread(
                key=self.mock_key, models=["mock-main", "mock-small"],
                scenarios=mock_gateway.load_scenarios([MOCK_DIR / "scenarios" / "readiness.json"]),
                fault=self.fault, slow_seconds=3, log_path=str(self.mock_log),
                event_delay=0.01)
        else:
            self.base_url = "http://127.0.0.1:%d" % free_port()
        self.conf = self.tmp / "lab.conf"
        self.write_conf()
        self._old_env = dict(os.environ)
        os.environ.update(self.env())
        redact.reset()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._old_env)
        redact.reset()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        make_writable(self.tmp)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers --------------------------------------------------------
    def write_conf(self, overrides=None):
        values = {
            ("lab", "version"): "0.1.0-test",
            ("lab", "support_contact"): "the TA (test)",
            ("gateway", "base_url"): self.base_url,
            ("gateway", "model_main"): "mock-main",
            ("gateway", "model_small"): "mock-small",
            ("gateway", "connect_timeout_s"): "2",
            ("gateway", "request_timeout_s"): str(self.request_timeout),
            ("gateway", "network_hint"): "Connect to the VPN (test hint).",
            ("claude", "binary"): str(self.claude_binary or self.fake_claude),
            ("claude", "readiness_timeout_s"): "60",
            ("case", "id"): "sample-case",
            ("case", "title"): "Sample case",
            ("case", "dir"): str(self.case),
            ("requirements", "min_free_disk_gb"): "0",
            ("requirements", "min_ram_mb"): "1",
            ("requirements", "min_cpus"): "1",
            # Readiness check 5 on the subscription route connects here (the mock).
            ("subscription", "hosts"): self.base_url,
        }
        values.update(overrides or {})
        sections = {}
        for (sec, key), val in values.items():
            if val is None:
                continue
            sections.setdefault(sec, []).append("%s = %s" % (key, val))
        text = "\n".join("[%s]\n%s\n" % (s, "\n".join(lines)) for s, lines in sections.items())
        self.conf.write_text(text)
        return self.conf

    def env(self, **extra):
        e = {
            "HOME": str(self.home),
            "DFIR_LAB_CONF": str(self.conf),
            "CLAUDE_CONFIG_DIR": str(self.claude_dir),
            "FAKE_CLAUDE_LOG": str(self.fake_log),
            "FAKE_CLAUDE_AUTH_LOG": str(self.fake_auth_log),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for k in ("DISPLAY", "WAYLAND_DISPLAY"):
            e[k] = ""
        e.update(extra)
        return e

    def run_cli(self, *args, input=None, env=None, prog="dfir-lab", timeout=120):
        full_env = dict(os.environ)
        full_env.update(self.env())
        full_env.pop("DISPLAY", None)
        full_env.pop("WAYLAND_DISPLAY", None)
        if env:
            full_env.update(env)
        return subprocess.run([sys.executable, str(BIN / prog)] + list(args), input=input,
                              capture_output=True, text=True, env=full_env, timeout=timeout,
                              cwd=str(self.tmp))

    def store_key(self, key=None):
        key = key or self.mock_key
        r = self.run_cli("setup-key", "--from-stdin", input=key + "\n")
        redact.reset()
        return r

    FAKE_TOKEN = "sk-ant-oat01-faketokenfaketokenfaketoken"

    def set_route(self, name):
        """Store the route the way `dfir-lab use` does."""
        d = self.home / "dfir-lab"
        d.mkdir(parents=True, exist_ok=True)
        (d / "route.json").write_text(json.dumps({"route": name}))

    def fake_sign_in(self, token=None):
        """A sign-in file like Claude Code's (the fake claude reads it)."""
        self.claude_dir.mkdir(parents=True, exist_ok=True)
        p = self.claude_dir / ".credentials.json"
        p.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": token or self.FAKE_TOKEN,
            "refreshToken": "sk-ant-ort01-fakerefreshfakerefreshfake",
            "subscriptionType": "pro"}}))
        os.chmod(p, 0o600)
        redact.reset()
        return p

    def set_fault(self, mode):
        self.server.state.fault = mode

    def all_text_under(self, *roots):
        """Concatenated bytes of every file under the given roots."""
        chunks = []
        for root in roots:
            root = Path(root)
            if root.is_file():
                chunks.append(root.read_bytes())
                continue
            for dp, dn, fn in os.walk(root):
                for f in fn:
                    p = Path(dp) / f
                    if p.is_symlink():
                        continue
                    try:
                        chunks.append(p.read_bytes())
                    except OSError:
                        pass
        return b"\n".join(chunks)

    def assertNoKey(self, text, key=None):
        key = key or self.mock_key
        if isinstance(text, bytes):
            self.assertNotIn(key.encode(), text)
        else:
            self.assertNotIn(key, text)


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def gateway_fixture(name):
    """A response captured from the real Duke gateway (fixtures/gateway/<name>.json)."""
    return read_json(FIXTURES / "gateway" / (name + ".json"))
