"""Talking to the gateway, and classifying every failure into one outcome
kind that maps to one student-facing message (see messages.py).

Standard library only (http.client, socket, ssl).
"""

import http.client
import json
import re
import socket
import ssl
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

from . import TOOLKIT_VERSION

ANTHROPIC_VERSION = "2023-06-01"


# --------------------------------------------------------------------------
# Network reachability (check 5)
# --------------------------------------------------------------------------

@dataclass
class NetResult:
    ok: bool
    kind: str = "ok"          # ok | dns | timeout | refused | unreachable | cert | tls
    detail: str = ""
    host: str = ""
    port: int = 0
    scheme: str = "https"
    address: str = ""
    connect_ms: float = 0.0
    tls_version: str = ""


def parse_base(base_url):
    u = urllib.parse.urlsplit(base_url)
    scheme = (u.scheme or "https").lower()
    host = u.hostname or ""
    port = u.port or (443 if scheme == "https" else 80)
    prefix = u.path.rstrip("/")
    return scheme, host, port, prefix


def _resolve(host, port, timeout):
    """getaddrinfo has no timeout of its own; run it in a thread."""
    box = {}

    def run():
        try:
            box["res"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception as exc:  # noqa: BLE001 - reported to caller
            box["exc"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise socket.timeout("name lookup timed out")
    if "exc" in box:
        raise box["exc"]
    return box["res"]


def probe_network(base_url, timeout):
    scheme, host, port, _ = parse_base(base_url)
    res = NetResult(ok=False, host=host, port=port, scheme=scheme)
    try:
        infos = _resolve(host, port, timeout)
    except socket.timeout:
        res.kind, res.detail = "dns", "the name lookup took longer than %gs" % timeout
        return res
    except (socket.gaierror, UnicodeError, OSError) as exc:
        res.kind, res.detail = "dns", str(exc)
        return res
    if not infos:
        res.kind, res.detail = "dns", "no addresses returned"
        return res
    # Try every resolved address (e.g. a dead IPv6 address before a working
    # IPv4 one) within one overall deadline, sharing the remaining time fairly.
    deadline = time.monotonic() + timeout
    failures = []
    sock = None
    for i, (family, stype, proto, _, sockaddr) in enumerate(infos):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            failures.append(("timeout", sockaddr[0], "not tried: out of time"))
            continue
        s = socket.socket(family, stype, proto)
        s.settimeout(max(remaining / (len(infos) - i), 0.05))
        start = time.monotonic()
        try:
            s.connect(sockaddr)
        except socket.timeout:
            s.close()
            failures.append(("timeout", sockaddr[0], "no answer"))
            continue
        except ConnectionRefusedError as exc:
            s.close()
            failures.append(("refused", sockaddr[0], str(exc)))
            continue
        except OSError as exc:
            s.close()
            failures.append(("unreachable", sockaddr[0], str(exc)))
            continue
        res.connect_ms = (time.monotonic() - start) * 1000.0
        res.address = sockaddr[0]
        sock = s
        break
    if sock is None:
        kinds = {k for k, _, _ in failures}
        # The most telling reason wins: a timeout usually means "not on the
        # university network"; a refusal is a server-side problem.
        res.kind = next(k for k in ("timeout", "unreachable", "refused") if k in kinds)
        res.address = failures[0][1]
        if res.kind == "timeout" and len(failures) == 1:
            res.detail = "no answer within %gs" % timeout
        else:
            res.detail = "; ".join("%s: %s" % (a, d) for _, a, d in failures)
        return res
    remaining = max(deadline - time.monotonic(), 0.5)
    if scheme == "https":
        ctx = ssl.create_default_context()
        sock.settimeout(remaining)
        try:
            tls = ctx.wrap_socket(sock, server_hostname=host)
            res.tls_version = tls.version() or ""
            tls.close()
        except ssl.SSLCertVerificationError as exc:
            sock.close()
            res.kind = "cert"
            res.detail = getattr(exc, "verify_message", "") or str(exc)
            return res
        except socket.timeout:
            sock.close()
            res.kind, res.detail = "timeout", "the secure connection did not finish within %gs" % timeout
            return res
        except (ssl.SSLError, OSError) as exc:
            sock.close()
            res.kind, res.detail = "tls", str(exc)
            return res
    else:
        sock.close()
    res.ok, res.kind = True, "ok"
    return res


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

@dataclass
class HttpResult:
    status: int = 0
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    elapsed: float = 0.0
    net_error: str = ""       # dns | timeout | refused | unreachable | cert | tls | slow | closed
    net_detail: str = ""

    @property
    def text(self):
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None


@dataclass
class StreamResult(HttpResult):
    events: list = field(default_factory=list)   # (name, data_dict_or_None, t_seconds)
    complete: bool = False
    t_first: float = None
    t_stop: float = None
    reads: int = 0
    dropped: bool = False


def _net_kind(exc):
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "cert"
    if isinstance(exc, ssl.SSLError):
        return "tls"
    if isinstance(exc, socket.gaierror):
        return "dns"
    if isinstance(exc, socket.timeout):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    return "unreachable"


class Gateway:
    def __init__(self, base_url, key, auth_style="bearer", connect_timeout=8.0,
                 request_timeout=60.0):
        self.base_url = base_url
        self.key = key
        self.auth_style = auth_style
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.scheme, self.host, self.port, self.prefix = parse_base(base_url)
        self.last_date = None   # HTTP Date header of the last response

    @classmethod
    def from_config(cls, cfg, key):
        return cls(cfg.base_url, key, cfg.auth_style, cfg.connect_timeout, cfg.request_timeout)

    def headers(self, extra=None):
        h = {
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "dfir-lab/%s" % TOOLKIT_VERSION,
        }
        if self.key:
            if self.auth_style in ("bearer", "both"):
                h["authorization"] = "Bearer " + self.key
            if self.auth_style in ("x-api-key", "both"):
                h["x-api-key"] = self.key
        if extra:
            h.update(extra)
        return h

    def _connect(self):
        if self.scheme == "https":
            conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.connect_timeout,
                                               context=ssl.create_default_context())
        else:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=self.connect_timeout)
        conn.connect()
        conn.sock.settimeout(self.request_timeout)
        return conn

    def _open(self, method, path, payload, extra_headers, result):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        start = time.monotonic()
        try:
            conn = self._connect()
        except (OSError, http.client.HTTPException) as exc:
            result.net_error, result.net_detail = _net_kind(exc), str(exc)
            result.elapsed = time.monotonic() - start
            return None, None, start
        try:
            conn.request(method, self.prefix + path, body=body, headers=self.headers(extra_headers))
            resp = conn.getresponse()
        except socket.timeout as exc:
            result.net_error, result.net_detail = "slow", str(exc) or "timed out"
            result.elapsed = time.monotonic() - start
            conn.close()
            return None, None, start
        except (OSError, http.client.HTTPException) as exc:
            result.net_error, result.net_detail = "closed", str(exc) or exc.__class__.__name__
            result.elapsed = time.monotonic() - start
            conn.close()
            return None, None, start
        result.status = resp.status
        result.headers = {k.lower(): v for k, v in resp.getheaders()}
        self.last_date = result.headers.get("date") or self.last_date
        return conn, resp, start

    def request(self, method, path, payload=None, extra_headers=None):
        result = HttpResult()
        conn, resp, start = self._open(method, path, payload, extra_headers, result)
        if resp is None:
            return result
        try:
            result.body = resp.read() if method != "HEAD" else b""
        except socket.timeout as exc:
            result.net_error, result.net_detail = "slow", str(exc) or "timed out"
        except (OSError, http.client.HTTPException) as exc:
            result.net_error, result.net_detail = "closed", str(exc) or exc.__class__.__name__
        finally:
            conn.close()
        result.elapsed = time.monotonic() - start
        return result

    def models(self):
        return self.request("GET", "/v1/models")

    def messages(self, payload):
        return self.request("POST", "/v1/messages", payload)

    def stream_messages(self, payload):
        payload = dict(payload, stream=True)
        result = StreamResult()
        conn, resp, start = self._open("POST", "/v1/messages", payload,
                                       {"accept": "text/event-stream"}, result)
        if resp is None:
            return result
        if resp.status != 200:
            try:
                result.body = resp.read()
            except (OSError, http.client.HTTPException):
                pass
            conn.close()
            result.elapsed = time.monotonic() - start
            return result
        buf = ""
        raw = bytearray()
        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                now = time.monotonic() - start
                result.reads += 1
                raw.extend(chunk)
                buf += chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    ev = _parse_sse_block(block)
                    if ev is None:
                        continue
                    name, data = ev
                    result.events.append((name, data, now))
                    if result.t_first is None:
                        result.t_first = now
                    if name == "message_stop":
                        result.t_stop = now
                        result.complete = True
        except socket.timeout as exc:
            result.net_error, result.net_detail = "slow", str(exc) or "timed out"
        except (OSError, http.client.HTTPException) as exc:
            # IncompleteRead and friends: the stream was cut off.
            result.dropped = True
            result.net_detail = str(exc) or exc.__class__.__name__
        finally:
            conn.close()
        if not result.complete and not result.net_error:
            result.dropped = True
        result.body = bytes(raw)
        result.elapsed = time.monotonic() - start
        return result


def _parse_sse_block(block):
    name, datas = None, []
    for line in block.split("\n"):
        if not line or line.startswith(":"):
            continue
        key, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if key == "event":
            name = value
        elif key == "data":
            datas.append(value)
    if name is None and not datas:
        return None
    data = None
    if datas:
        try:
            data = json.loads("\n".join(datas))
        except ValueError:
            data = None
    if name is None and isinstance(data, dict):
        name = data.get("type")
    return name or "message", data


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
#
# Formats, from LiteLLM's current source (BerriAI/litellm main:
# proxy/common_utils/anthropic_error_payload.py, proxy/auth/model_access_denied.py,
# proxy/_types.py ProxyErrorTypes) and https://docs.litellm.ai/docs/proxy/error_reference,
# read 2026-11-13:
#   /v1/messages     {"type": "error", "error": {"type": <Anthropic error type>,
#                     "message": "...", "litellm_error_type": "<ProxyErrorTypes>"}}
#                    (litellm_error_type only when it adds information)
#   other routes     {"error": {"message": "...", "type": "...", "param": ..., "code": "429"}}
#   missing route    {"detail": "Not Found"}
# The university gateway's real responses have never been sampled. To support
# a newly captured response: add or adjust one step in classify() (structured
# fields first, message text second) and add the captured body to
# ClassifyTests in tests/test_check.py.

@dataclass
class Outcome:
    kind: str                 # see messages.OUTCOMES
    status: int = 0
    detail: str = ""
    retry_after: str = ""


@dataclass
class ErrorInfo:
    type: str = ""            # error.type, lower case
    litellm_type: str = ""    # error.litellm_error_type, lower case
    message: str = ""         # error.message (or detail/message/the body)
    has_error: bool = False   # the body is JSON with an "error" member
    text: str = ""            # lower-case message + start of the body, for matching only


def error_info(res):
    data = res.json()
    info = ErrorInfo()
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            info.has_error = True
            info.type = str(err.get("type") or "").lower()
            info.litellm_type = str(err.get("litellm_error_type") or "").lower()
            info.message = str(err.get("message") or err.get("type") or "")
        elif isinstance(err, str):
            info.has_error = True
            info.message = err
        elif "detail" in data:
            info.message = str(data["detail"])
        elif "message" in data:
            info.message = str(data["message"])
    if not info.message:
        info.message = " ".join(res.text.split())
    info.text = (info.message + " " + res.text[:4000]).lower()
    return info


def error_message(res):
    """Best-effort extraction of the error text a gateway returned (not
    redacted or shortened here; messages.render does both, in that order)."""
    return error_info(res).message


def _has(text, needles):
    return any(n in text for n in needles)


# Message fragments (lower case). Structured fields are checked before these.
_MODEL_ACCESS = ("not allowed to access model", "can only access models",
                 "is not available for this api key", "not in the allowed models",
                 "model access denied", "does not have access to model",
                 "not authorized to access model")
_BUSY = ("no deployments available", "no healthy deployments", "cooldown", "cooling down",
         "server_is_overloaded", "overloaded")
_BUSY_TYPES = {"overloaded_error", "service_unavailable_error"}
_BUDGET = ("budget has been exceeded", "exceededbudget", "over budget", "max budget",
           "max_budget", "spend limit", "insufficient_quota", "quota exceeded")
_GATEWAY_AUTH_TYPES = {"token_not_found_in_db", "expired_key", "auth_error"}
_GATEWAY_AUTH = ("invalid proxy server token", "litellm_verificationtokentable",
                 "unable to find token", "expired key", "key expired", "virtual key")
_MODEL_UNKNOWN = ("invalid model name", "invalid model", "model not found", "model_not_found",
                  "unknown model", "no such model", "model does not exist")
_UNSUPPORTED = re.compile(
    r"unsupportedparams|does not support (the )?param|doesn't support (the )?param"
    r"|unsupported (param|field|argument|beta|value|content)|unknown (param|field|argument)"
    r"|unrecognized (request )?(param|field|argument)|extra inputs are not permitted"
    r"|unexpected keyword|\b(param|parameter|field|argument|beta)s?\b[^.\n]{0,60}"
    r"\bnot (supported|permitted|allowed)\b")
# A provider's own exception named in a LiteLLM message, e.g.
# "litellm.AuthenticationError: AnthropicException - invalid x-api-key".
_PROVIDER_EXC = re.compile(r"\b(?!proxy|http)[a-z0-9_]*[a-z]exception\b")


def classify(res, model=None, route="messages"):
    """Map an HttpResult to an Outcome. `route` is 'models' or 'messages'.

    Order: network failures, success, then gateway errors with structured
    fields (error.type, litellm_error_type) before message text. When the
    cause cannot be told apart the outcome says so (kind 'other' or
    'unclear_404'), and the student sees the HTTP status and redacted body."""
    if res.net_error:
        return Outcome(kind=res.net_error, detail=res.net_detail)
    s = res.status
    if 200 <= s < 300:
        return Outcome(kind="ok", status=s)
    info = error_info(res)
    low = info.text
    types = {t for t in (info.type, info.litellm_type) if t}
    retry = res.headers.get("retry-after", "")

    def out(kind):
        return Outcome(kind=kind, status=s, detail=info.message, retry_after=retry)

    # Model access denied at any layer (key, team, user, org, project, ...).
    if any(t.endswith("_model_access_denied") for t in types) or _has(low, _MODEL_ACCESS):
        return out("model_forbidden")
    # The service behind the gateway is busy or down: no healthy deployment,
    # every deployment cooling down, overloaded. Not the student's limit.
    if s in (503, 529) or types & _BUSY_TYPES or _has(low, _BUSY):
        return out("busy")
    if "budget_exceeded" in types or _has(low, _BUDGET) or ("budget" in low and "exceed" in low):
        return out("limit")
    if s in (401, 403):
        gateway_auth = bool(types & _GATEWAY_AUTH_TYPES) or _has(low, _GATEWAY_AUTH)
        if not gateway_auth and _PROVIDER_EXC.search(low):
            return out("upstream_auth")     # the provider's credential, probably not the key
        if s == 403 and info.litellm_type and not gateway_auth:
            return out("other")             # some other permission (e.g. a tool), not the key
        return out("key_rejected")
    if s == 429:
        if _PROVIDER_EXC.search(low) and not retry:
            return out("busy")              # the provider throttled the gateway
        return out("limit")
    if s in (400, 422):
        if _UNSUPPORTED.search(low):
            return out("request_rejected")
        if _has(low, _MODEL_UNKNOWN):
            return out("model_unknown")
        return out("other")
    if s == 404:
        if route == "models":
            return out("models_404")
        if not info.has_error:              # {"detail": "Not Found"}, an HTML page, nothing
            return out("no_anthropic_route")
        names_model = bool(model) and re.search(
            r"(?<![\w.-])%s(?![\w.-])" % re.escape(model.lower()), info.message.lower())
        if _has(low, _MODEL_UNKNOWN) or re.search(r"\bmodel\s*[:=]", low) or names_model:
            return out("model_unknown")
        return out("unclear_404")
    if s >= 500:
        return out("server_error")
    return out("other")


def model_ids(res):
    data = res.json()
    if not isinstance(data, dict):
        return None
    items = data.get("data")
    if not isinstance(items, list):
        return None
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            out.append(str(it["id"]))
        elif isinstance(it, str):
            out.append(it)
    return out


def similar_models(wanted, available, n=4):
    import difflib
    close = difflib.get_close_matches(wanted, available, n=n, cutoff=0.4)
    tokens = [t for t in wanted.lower().replace("_", "-").split("-") if len(t) > 2]
    for m in available:
        if m not in close and any(t in m.lower() for t in tokens):
            close.append(m)
    return close[:n]


def usage_tokens(obj):
    """(input, output) from an Anthropic message or usage dict."""
    if not isinstance(obj, dict):
        return 0, 0
    u = obj.get("usage", obj)
    if not isinstance(u, dict):
        return 0, 0
    def n(k):
        v = u.get(k)
        return int(v) if isinstance(v, (int, float)) else 0
    inp = n("input_tokens") + n("cache_creation_input_tokens") + n("cache_read_input_tokens")
    return inp, n("output_tokens")
