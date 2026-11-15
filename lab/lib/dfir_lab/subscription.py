"""The optional subscription route: Claude Code signed in with the student's
own Claude subscription (Pro, Max or higher) instead of the Duke gateway.

How Claude Code 2.1.322 signs in (code.claude.com/docs/en/authentication and
/cli-reference, read 2026-11-14; output observed with the pinned binary):

- `claude auth login --claudeai` prints "Opening browser to sign in…",
  opens the sign-in page with $BROWSER (else xdg-open), prints
  "If the browser didn't open, visit: <link>" and "Paste code here if
  prompted > ". After the student signs in and authorises, the browser
  hands the result back to a listener on localhost; if it shows a code
  instead, the student pastes it. It ends with "Login successful." (exit 0)
  or "Login failed: ..." (exit 1).
- The sign-in is stored in ~/.claude/.credentials.json (mode 0600), or under
  $CLAUDE_CONFIG_DIR. This module never opens that file.
- `claude auth status --json` prints loggedIn and authMethod (documented:
  none, claude.ai, oauth_token, api_key, api_key_helper, third_party) and
  exits 0 when signed in, 1 when not. For a claude.ai sign-in 2.1.322 also
  prints subscriptionType, email, orgName and loginExpiresAt (observed in the
  binary, not documented). Only authMethod, subscriptionType and
  loginExpiresAt are used; the email is shown to the student once and never
  written anywhere.
- Signing out: `claude auth logout` (or /logout inside Claude Code).
"""

import datetime as _dt
import json
import re
import subprocess
from dataclasses import dataclass

from . import fsutil, route as route_mod, ui
from .claude_env import process_env
from .errors import LabError
from .redact import redact

# authMethod values that mean "a Claude subscription": a /login sign-in
# (claude.ai) or a CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`, which
# "authenticates with your Claude subscription" (authentication docs).
SUBSCRIPTION_METHODS = ("claude.ai", "oauth_token")
STATUS_TIMEOUT_S = 45
SOON_S = 24 * 3600

METHOD_TEXT = {
    "api_key": "an Anthropic API key (Console, billed per use)",
    "api_key_helper": "the Duke key helper",
    "third_party": "a cloud provider (Bedrock, Vertex or Foundry)",
}


@dataclass
class AuthState:
    ok: bool = False            # `claude auth status --json` ran and printed JSON
    signed_in: bool = False     # signed in with a subscription and not expired
    logged_in: bool = False     # Claude Code's own loggedIn
    method: str = "none"
    plan: str = ""
    email: str = ""
    expires: str = ""
    expired: bool = False
    ends_soon: bool = False
    detail: str = ""

    @property
    def plan_text(self):
        return plan_text(self.plan)


def plan_text(plan):
    """'pro' -> 'Claude Pro'. Unknown or missing -> 'Claude'."""
    p = (plan or "").strip().replace("_", " ")
    return "Claude %s" % p.title() if p else "Claude"


def model_text(cfg):
    m = cfg.subscription_model
    if m.lower() == "sonnet":
        return "Claude Sonnet (the current Sonnet model)"
    return m


def _parse_time(text):
    try:
        t = _dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def parse_status(text, now=None):
    """AuthState from the JSON `claude auth status --json` printed."""
    st = AuthState()
    raw = (text or "").strip()
    start = raw.find("{")
    try:
        data = json.loads(raw[start:]) if start >= 0 else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        st.detail = "Claude Code did not report its sign-in state"
        return st
    st.ok = True
    st.logged_in = data.get("loggedIn") is True
    st.method = str(data.get("authMethod") or "none")
    st.plan = str(data.get("subscriptionType") or "")
    st.email = str(data.get("email") or "")
    st.expires = str(data.get("loginExpiresAt") or "")
    when = _parse_time(st.expires) if st.expires else None
    now = now or fsutil.utc_now()
    if when is not None:
        left = (when - now).total_seconds()
        st.expired = left <= 0
        st.ends_soon = 0 < left < SOON_S
    st.signed_in = st.logged_in and st.method in SUBSCRIPTION_METHODS and not st.expired
    return st


def auth_status(cfg, exe):
    """Ask Claude Code whether it is signed in with a subscription, with the
    environment the subscription route gives it. Never raises."""
    try:
        p = subprocess.run([exe, "auth", "status", "--json"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                           timeout=STATUS_TIMEOUT_S,
                           env=process_env(cfg, route=route_mod.SUBSCRIPTION))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AuthState(detail="could not run claude auth status (%s)" % exc.__class__.__name__)
    st = parse_status(p.stdout)
    if not st.ok:
        lines = [l for l in redact(p.stderr or "").splitlines() if l.strip()]
        st.detail = "%s (exit %d%s)" % (st.detail, p.returncode,
                                        (": " + lines[-1][:200]) if lines else "")
    return st


def describe(st):
    """One plain line about the sign-in, without the email."""
    if not st.ok:
        return st.detail or "the sign-in state is unknown"
    if st.expired:
        return "the Claude sign-in has expired"
    if st.signed_in:
        how = "with a %s subscription" % st.plan_text if st.method == "claude.ai" else \
            "with a Claude subscription token (CLAUDE_CODE_OAUTH_TOKEN)"
        return "signed in %s" % how
    if st.logged_in:
        return "signed in with %s, not with a Claude subscription" % METHOD_TEXT.get(
            st.method, st.method)
    return "not signed in"


def sign_in(cfg, exe):
    """Run Claude Code's own sign-in in this terminal. Returns its exit code.

    Only `claude auth login` handles the credential; the toolkit shows its
    output as it is and never reads or stores the sign-in."""
    if not ui.interactive():
        raise LabError("Signing in to your Claude account needs a terminal window.",
                       "Double-click \"Use my Claude subscription\" on the desktop, or run "
                       "dfir-lab use subscription  in a terminal.")
    env = process_env(cfg, route=route_mod.SUBSCRIPTION)
    browser = "Chromium" if env.get("BROWSER") else "your web browser"
    ui.out("Signing in to your Claude account. What happens now:")
    ui.out("  1. %s opens a Claude sign-in page. Log in with the account that has your "
           "subscription and click Authorize." % browser)
    ui.out("  2. Come back to this window: it says \"Login successful\" when you are done.")
    ui.out("  If no browser opens, copy the link printed below into Chromium yourself. If the "
           "page then shows a code, paste it here and press Enter.")
    ui.out("")
    fsutil.log("subscription.sign-in.start")
    try:
        rc = subprocess.call([exe, "auth", "login", "--claudeai"], env=env)
    except OSError as exc:
        raise LabError("Could not start Claude Code's sign-in (%s)." % (exc.strerror or exc),
                       "Tell the TA.")
    fsutil.log("subscription.sign-in.end", exit=rc)
    ui.out("")
    return rc


# --------------------------------------------------------------------------
# What Claude Code reports when the subscription route fails
# --------------------------------------------------------------------------
# Formats seen with Claude Code 2.1.322 (no account needed): "Not logged in ·
# Please run /login"; "Failed to authenticate. API Error: 401 OAuth access token
# is invalid."; "API Error: Connection refused — a firewall or proxy may be
# blocking it (ECONNREFUSED)". From the binary and code.claude.com/docs/en/errors
# (not reproducible without an account): "Login expired · Please run /login",
# "OAuth token revoked · Please run /login", "You've hit your session limit ·
# resets 3:45pm" (also weekly, Opus, Sonnet limits), "Usage limit reached",
# "Request rejected (429)".

_EXPIRED = re.compile(r"login expired|session expired|reached its time limit|token revoked|"
                      r"token has expired|access token is invalid|failed to authenticate|"
                      r"authentication required|re-?authenticate|token refresh failed|"
                      r"api error: 401|sign in again", re.I)
_LIMIT = re.compile(r"you.ve hit your|usage limit reached|usage limit|spend limit|"
                    r"\blimit reached\b", re.I)
_NOT_SIGNED_IN = re.compile(r"not logged in|please run /login|run /login", re.I)
_NETWORK = re.compile(r"unable to connect|connection refused|econnrefused|enotfound|"
                      r"etimedout|eai_again|can.t reach the api server|no internet route|"
                      r"couldn.t connect through your proxy|connection dropped|getaddrinfo|"
                      r"network is unreachable", re.I)
_BUSY = re.compile(r"request rejected \(429\)|overloaded|\b529\b|temporary capacity", re.I)


def classify_text(text):
    """The outcome kind for an error Claude Code printed on the subscription
    route, or None when it is not one of them."""
    t = text or ""
    if _LIMIT.search(t) and not _NOT_SIGNED_IN.search(t):
        return "sub_usage_limit"
    if _EXPIRED.search(t):
        return "sub_login_expired"
    if _NOT_SIGNED_IN.search(t):
        return "sub_not_signed_in"
    if _NETWORK.search(t):
        return "sub_no_network"
    if _BUSY.search(t):
        return "sub_busy"
    return None


NET_REASON = {
    "dns": "could not be looked up (no name lookup)",
    "timeout": "did not answer in time",
    "refused": "refused the connection",
    "unreachable": "has no network route",
    "cert": "failed its certificate check",
    "tls": "could not open a secure connection",
}


def net_fields(net):
    """Template fields for a failed probe of an Anthropic host."""
    return {"host": net.host, "port": net.port,
            "reason": NET_REASON.get(net.kind, "could not be reached"),
            "detail": net.detail}
