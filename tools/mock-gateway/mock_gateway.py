#!/usr/bin/env python3
"""Mock of the Anthropic Messages API as served by a LiteLLM gateway.

Standard library only. Used by the dfir-lab tests and by anyone developing
without access to the real gateway.

Routes
  GET  /v1/models                    model list (LiteLLM/Anthropic shape)
  GET  /key/info                     the key's own limits (LiteLLM shape)
  POST /v1/messages[?beta=true]      plain JSON or server-sent events
  POST /v1/messages/count_tokens     rough token count
  HEAD /api/hello                    connection warm-up probe
  POST /v1/chat/completions          OpenAI-style route (exists in every mode)
  GET  /_mock/state                  current fault and request count (no auth)
  POST /_mock/fault                  {"mode": "<fault>"} sets the fault (no auth)
  POST /_mock/reset                  clears the fault (no auth)

A request can also select a fault for itself with the header
  x-mock-fault: <fault>

Faults
  none, wrong-key, forbidden-model, unknown-model-404, unknown-model-400,
  rate-limit, budget, server-error, slow-first-byte, buffered-stream,
  dropped-stream, cached-replay, no-anthropic-route, unsupported-param,
  provider-reject, no-healthy-deployments, cooldown, upstream-auth

Formats copied from the real Duke gateway (LiteLLM 1.108.7, sampled
2026-11-14; the bodies are in lab/tests/fixtures/gateway/):
  - wrong or missing key: 401 in the OpenAI-style envelope
    {"error": {"message", "type": "token_not_found_in_db"|"auth_error", "param", "code"}}
    on every route, /v1/messages included;
  - a model the key may not use, or that does not exist: 403, same envelope,
    type "team_model_access_denied" (forbidden-model, and any unknown model);
  - per-minute limit: 429 {"type": "error", "error": {"type": "rate_limit_error",
    "message": "LiteLLM Rate Limit Handler ... current tpm: N, tpm limit: 40000 ..."}},
    no retry-after header (rate-limit);
  - a field the provider rejects: 400 Anthropic envelope wrapping the
    provider's own error JSON (provider-reject);
  - a repeated identical request: replayed from the response cache, a stream
    all at once with a ping first and an x-litellm-cache-key header (cached-replay);
  - x-litellm-key-{rpm,tpm}-limit, -max-budget and -spend headers on answers.
Not sampled, so approximations from LiteLLM's source and docs: budget,
unknown-model-404/-400, unsupported-param, no-healthy-deployments, cooldown,
upstream-auth, server-error.

Scenarios
  A scenario file lists the replies to return, in order, for requests that
  carry tools (Claude Code always sends tools):
  {"name": "readiness", "match": "readiness.sh",
   "steps": [{"tool_use": {"name": "Bash", "input": {"command": "sh readiness.sh"}}},
             {"text": "DONE"}]}
  The step is chosen from the number of assistant turns already in the
  request, so the server is stateless and Claude Code retries are harmless.

The request log (JSON Lines) records method, path, model, whether streamed,
status and fault. It never records the Authorization header, and every line
is redacted (the configured key, also JSON-escaped, becomes [REDACTED-KEY]).
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAULTS = ("none", "wrong-key", "forbidden-model", "unknown-model-404", "unknown-model-400",
          "rate-limit", "budget", "server-error", "slow-first-byte", "buffered-stream",
          "dropped-stream", "cached-replay", "no-anthropic-route", "unsupported-param",
          "provider-reject", "no-healthy-deployments", "cooldown", "upstream-auth")

# The Duke key's limits (GET /key/info, 2026-11-14).
KEY_LIMITS = {"rpm_limit": 20, "tpm_limit": 40000, "max_budget": 1.0, "budget_duration": "24h",
              "max_parallel_requests": 40}

# HTTP status -> Anthropic error type, as in the Anthropic API.
ANTHROPIC_TYPES = {400: "invalid_request_error", 401: "authentication_error",
                   403: "permission_error", 404: "not_found_error", 429: "rate_limit_error",
                   500: "api_error", 529: "overloaded_error"}
MARK = "[REDACTED-KEY]"


class MockState:
    def __init__(self, key, models, scenarios=(), fault="none", slow_seconds=5.0,
                 log_path=None, event_delay=0.02, restricted_models=("other-model",)):
        self.key = key
        self.models = list(models)
        self.scenarios = list(scenarios)
        self.fault = fault
        self.slow_seconds = slow_seconds
        self.log_path = log_path
        self.event_delay = event_delay
        self.restricted_models = list(restricted_models)
        self.lock = threading.Lock()
        self.requests = 0

    def log(self, **rec):
        with self.lock:
            self.requests += 1
            if not self.log_path:
                return
            rec["time"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            line = json.dumps(rec)
            if self.key:
                # Request fields (model, path) can carry the key; never log it.
                for form in (self.key, json.dumps(self.key)[1:-1]):
                    line = line.replace(form, MARK)
            try:
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass


def load_scenarios(paths):
    out = []
    for p in paths or []:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        out.extend(data if isinstance(data, list) else [data])
    return out


def _err(message, etype, code, param=None):
    """OpenAI-style LiteLLM error (routes other than /v1/messages)."""
    return {"error": {"message": message, "type": etype, "param": param, "code": str(code)}}


def _anthropic_err(status, message, litellm_type=None):
    """Anthropic-style envelope LiteLLM returns on /v1/messages."""
    err = {"type": ANTHROPIC_TYPES.get(status, "api_error"), "message": message}
    if litellm_type:
        err["litellm_error_type"] = litellm_type
    return {"type": "error", "error": err}


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    out.append(b.get("text", ""))
                elif b.get("type") == "tool_result":
                    out.append(_text_of(b.get("content")))
        return "\n".join(out)
    return ""


def _sample_value(schema):
    t = (schema or {}).get("type")
    if t == "string":
        return "UTC"
    if t in ("integer", "number"):
        return 1
    if t == "boolean":
        return True
    if t == "array":
        return []
    if t == "object":
        return {k: _sample_value(v) for k, v in (schema.get("properties") or {}).items()
                if k in (schema.get("required") or [])}
    return "example"


def _tokens(text):
    return max(1, len(text) // 4)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mock-litellm/0.1"
    state = None  # set by make_server

    # -- plumbing -------------------------------------------------------
    def log_message(self, fmt, *args):  # silence default stderr logging
        pass

    def _read_json(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return None

    def _send_json(self, status, obj, extra_headers=None):
        body = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The client gave up (e.g. its timeout during slow-first-byte).
            self.close_connection = True

    def _error(self, status, message, litellm_type=None, extra_headers=None, param=None):
        """An error in the shape LiteLLM uses for this route."""
        route = self._route()
        if route.startswith("/v1/messages") or (route == "/v1/models"
                                                and self.headers.get("anthropic-version")):
            obj = _anthropic_err(status, message, litellm_type)
        else:
            obj = _err(message, litellm_type or ANTHROPIC_TYPES.get(status), status, param)
        return self._send_json(status, obj, extra_headers)

    def _auth(self):
        """Returns 'ok', 'missing' or 'bad'. Never logs or echoes the key."""
        auth = self.headers.get("authorization") or ""
        xkey = self.headers.get("x-api-key") or ""
        presented = []
        if auth.lower().startswith("bearer "):
            presented.append(auth[7:].strip())
        if xkey:
            presented.append(xkey.strip())
        if not presented:
            return "missing", ""
        if any(p == self.state.key for p in presented):
            return "ok", presented[0]
        return "bad", presented[0]

    def _fault(self):
        return (self.headers.get("x-mock-fault") or self.state.fault or "none").strip()

    def _route(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path.rstrip("/") or "/"

    def _auth_error(self, presented):
        # The Duke gateway's exact bodies (2026-11-14), in the OpenAI-style
        # envelope on every route.
        if not presented:
            return self._send_json(401, _err("Authentication Error, No api key passed in.",
                                             "auth_error", 401, "None"))
        return self._send_json(401, _err("Authentication Error, Invalid API key provided.",
                                         "token_not_found_in_db", 401, "key"))

    def _model_denied(self, model):
        # The Duke gateway's answer for a model the key may not use and for a
        # model name it does not know (2026-11-14).
        return self._send_json(403, _err(
            "The requested model '%s' is not available for this API key, or the model name "
            "is invalid. Check the models available to you and try again." % model,
            "team_model_access_denied", 403, "model"))

    def _limit_headers(self):
        return {"x-litellm-key-rpm-limit": str(KEY_LIMITS["rpm_limit"]),
                "x-litellm-key-tpm-limit": str(KEY_LIMITS["tpm_limit"]),
                "x-litellm-key-max-budget": str(KEY_LIMITS["max_budget"]),
                "x-litellm-key-spend": "0.0123", "x-litellm-version": "1.108.7"}

    # -- verbs ----------------------------------------------------------
    def do_HEAD(self):
        route = self._route()
        if route == "/api/hello":
            self.state.log(method="HEAD", path=route, status=200)
            self.send_response(200)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        self.state.log(method="HEAD", path=route, status=404)
        self._send_json(404, {"detail": "Not Found"})

    def do_GET(self):
        route = self._route()
        if route == "/_mock/state":
            return self._send_json(200, {"fault": self.state.fault,
                                         "requests": self.state.requests})
        if route == "/api/hello":
            self.state.log(method="GET", path=route, status=200)
            return self._send_json(200, {"status": "ok"})
        if route == "/v1/models":
            return self._models()
        if route == "/key/info":
            return self._key_info()
        self.state.log(method="GET", path=route, status=404)
        self._send_json(404, {"detail": "Not Found"})

    def do_POST(self):
        route = self._route()
        if route.startswith("/_mock/"):
            return self._control(route)
        body = self._read_json()
        if route == "/v1/messages":
            return self._messages(body)
        if route == "/v1/messages/count_tokens":
            return self._count_tokens(body)
        if route == "/v1/chat/completions":
            return self._chat(body)
        self.state.log(method="POST", path=route, status=404)
        self._send_json(404, {"detail": "Not Found"})

    # -- control --------------------------------------------------------
    def _control(self, route):
        body = self._read_json() or {}
        if route == "/_mock/fault":
            mode = body.get("mode", "none")
            if mode not in FAULTS:
                return self._send_json(400, {"error": "unknown fault %r" % mode,
                                             "faults": FAULTS})
            self.state.fault = mode
            return self._send_json(200, {"fault": mode})
        if route == "/_mock/reset":
            self.state.fault = "none"
            return self._send_json(200, {"fault": "none"})
        return self._send_json(404, {"detail": "Not Found"})

    # -- routes ---------------------------------------------------------
    def _common_faults(self, fault, route, model, stream):
        """Faults that apply to every authenticated route. Returns True if handled."""
        if fault == "rate-limit":
            self.state.log(method=self.command, path=route, model=model, stream=stream,
                           status=429, fault=fault)
            # The Duke gateway's per-key limiter, exactly (2026-11-14): Anthropic
            # envelope on every route, no retry-after header.
            body = {"type": "error", "error": {"type": "rate_limit_error", "message": (
                "LiteLLM Rate Limit Handler for rate limit type = key. Crossed TPM / RPM / Max "
                "Parallel Request Limit. current rpm: 2, rpm limit: %d, current tpm: 52397, "
                "tpm limit: %d, current max_parallel_requests: 0, max_parallel_requests: %d"
                % (KEY_LIMITS["rpm_limit"], KEY_LIMITS["tpm_limit"],
                   KEY_LIMITS["max_parallel_requests"]))},
                "request_id": str(uuid.uuid4())}
            self._send_json(429, body, dict(self._limit_headers(), **{"x-litellm-response-cost": "0"}))
            return True
        if fault == "budget":
            self.state.log(method=self.command, path=route, model=model, stream=stream,
                           status=400, fault=fault)
            # NOT sampled from the real gateway (it would have meant spending the
            # key's budget). LiteLLM raises budget_exceeded from the same
            # key-check code as the auth errors, which came back in the
            # OpenAI-style envelope; this mirrors that.
            self._send_json(400, _err("Budget has been exceeded! Current cost: 1.0003, Max "
                                      "budget: 1.0", "budget_exceeded", 400, None),
                            self._limit_headers())
            return True
        if fault == "server-error":
            self.state.log(method=self.command, path=route, model=model, stream=stream,
                           status=500, fault=fault)
            self._error(500, "Internal Server Error", "internal_server_error")
            return True
        return False

    def _routing_faults(self, fault, route, model, stream):
        """Faults from the gateway's router or the provider behind it (Messages only)."""
        replies = {
            "unsupported-param": (400, "litellm.UnsupportedParamsError: %s does not support "
                                       "parameters: ['tool_choice']. To drop these, set "
                                       "`litellm.drop_params=True`." % model, None, None),
            "no-healthy-deployments": (400, "There are no healthy deployments for this model "
                                            "%s" % model, None, None),
            "cooldown": (429, "No deployments available for selected model, Try again in 60 "
                              "seconds. Passed model=%s. pre-call-checks=False, "
                              "cooldown_list=[('deployment-1', 60)]" % model, None,
                         {"retry-after": "60"}),
            "upstream-auth": (401, "litellm.AuthenticationError: AnthropicException - "
                                   "{\"type\":\"error\",\"error\":{\"type\":\"authentication_"
                                   "error\",\"message\":\"invalid x-api-key\"}}", None, None),
        }
        if fault not in replies:
            return False
        status, message, ltype, headers = replies[fault]
        self.state.log(method=self.command, path=route, model=model, stream=stream,
                       status=status, fault=fault)
        self._error(status, message, ltype, headers)
        return True

    def _models(self):
        route = "/v1/models"
        fault = self._fault()
        auth, presented = self._auth()
        if fault == "wrong-key" or auth != "ok":
            self.state.log(method="GET", path=route, status=401, fault=fault, auth=auth)
            return self._auth_error(presented)
        if self._common_faults(fault, route, None, False):
            return
        models = self.state.restricted_models if fault == "forbidden-model" else self.state.models
        self.state.log(method="GET", path=route, status=200, fault=fault)
        data = [{"id": m, "type": "model", "display_name": m, "created_at": "2026-01-01T00:00:00Z",
                 "object": "model", "owned_by": "openai"} for m in models]
        self._send_json(200, {"data": data, "object": "list", "has_more": False,
                              "first_id": models[0] if models else None,
                              "last_id": models[-1] if models else None})

    def _key_info(self):
        auth, presented = self._auth()
        if self._fault() == "wrong-key" or auth != "ok":
            self.state.log(method="GET", path="/key/info", status=401, auth=auth)
            return self._auth_error(presented)
        self.state.log(method="GET", path="/key/info", status=200)
        info = dict(KEY_LIMITS, spend=0.0123, budget_reset_at="2026-11-15T05:00:00+00:00",
                    key_name="sk-...%s" % self.state.key[-4:], models=["all-team-models"],
                    expires="2027-03-14T23:51:12+00:00")
        return self._send_json(200, {"key": "hashed", "info": info})

    def _count_tokens(self, body):
        route = "/v1/messages/count_tokens"
        fault = self._fault()
        auth, presented = self._auth()
        if fault == "wrong-key" or auth != "ok":
            self.state.log(method="POST", path=route, status=401, fault=fault, auth=auth)
            return self._auth_error(presented)
        if fault == "no-anthropic-route":
            self.state.log(method="POST", path=route, status=404, fault=fault)
            return self._send_json(404, {"detail": "Not Found"})
        text = json.dumps((body or {}).get("messages", "")) + json.dumps((body or {}).get("system", ""))
        self.state.log(method="POST", path=route, model=(body or {}).get("model"), status=200)
        self._send_json(200, {"input_tokens": _tokens(text)})

    def _chat(self, body):
        route = "/v1/chat/completions"
        auth, presented = self._auth()
        if auth != "ok":
            self.state.log(method="POST", path=route, status=401, auth=auth)
            return self._auth_error(presented)
        model = (body or {}).get("model")
        self.state.log(method="POST", path=route, model=model, status=200)
        self._send_json(200, {"id": "chatcmpl-mock", "object": "chat.completion", "model": model,
                              "choices": [{"index": 0, "finish_reason": "stop",
                                           "message": {"role": "assistant", "content": "OK"}}],
                              "usage": {"prompt_tokens": 5, "completion_tokens": 1,
                                        "total_tokens": 6}})

    def _messages(self, body):
        route = "/v1/messages"
        fault = self._fault()
        if body is None:
            self.state.log(method="POST", path=route, status=400)
            return self._error(400, "Invalid JSON body")
        model = body.get("model")
        stream = bool(body.get("stream"))
        if fault == "no-anthropic-route":
            self.state.log(method="POST", path=route, model=model, stream=stream, status=404,
                           fault=fault)
            return self._send_json(404, {"detail": "Not Found"})
        auth, presented = self._auth()
        if fault == "wrong-key" or auth != "ok":
            self.state.log(method="POST", path=route, model=model, stream=stream, status=401,
                           fault=fault, auth=auth)
            return self._auth_error(presented)
        if self._common_faults(fault, route, model, stream):
            return
        if fault == "forbidden-model":
            self.state.log(method="POST", path=route, model=model, stream=stream, status=403,
                           fault=fault)
            return self._model_denied(model)
        if fault == "unknown-model-404":
            self.state.log(method="POST", path=route, model=model, stream=stream, status=404,
                           fault=fault)
            # The provider's not-found body, relayed by LiteLLM.
            return self._error(404, "model: %s" % model)
        if fault == "unknown-model-400":
            self.state.log(method="POST", path=route, model=model, stream=stream, status=400,
                           fault=fault)
            return self._error(400, "/v1/messages: Invalid model name passed in model=%s. Call "
                               "`/v1/models` to view available models for your key." % model)
        if model not in self.state.models:
            self.state.log(method="POST", path=route, model=model, stream=stream, status=403,
                           fault=fault)
            return self._model_denied(model)
        if fault == "provider-reject":
            self.state.log(method="POST", path=route, model=model, stream=stream, status=400,
                           fault=fault)
            # The Duke gateway relaying the provider's own validation error (2026-11-14).
            inner = json.dumps({"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "output_config.effort: Input should be 'low', 'medium', 'high', "
                           "'xhigh' or 'max'"}, "request_id": "req_mock"})
            return self._send_json(400, {"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "litellm.BadRequestError: AnthropicException - %s\n\nLiteLLM: model "
                           "group '%s' failed with the error above. No fallback was attempted."
                           % (inner, model)}, "request_id": str(uuid.uuid4())},
                self._limit_headers())
        if self._routing_faults(fault, route, model, stream):
            return
        if fault == "slow-first-byte":
            time.sleep(self.state.slow_seconds)
        blocks, stop_reason = self._reply(body)
        # Facts about the last tool result, as booleans only (never its text,
        # which could contain a secret if something went wrong).
        last_result = ""
        for m in reversed(body.get("messages") or []):
            if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), list) \
                    and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"]):
                last_result = _text_of(m["content"])
                break
        self.state.log(method="POST", path=route, model=model, stream=stream, status=200,
                       fault=fault, tools=len(body.get("tools") or []),
                       reply=[b["type"] for b in blocks],
                       tool_result_seen=bool(last_result),
                       tool_result_blocked_by_guard="Blocked by the DFIR lab" in last_result,
                       tool_result_contains_key=bool(self.state.key) and self.state.key in last_result)
        in_tok = _tokens(json.dumps(body.get("messages", "")))
        out_tok = sum(_tokens(b.get("text", "") or json.dumps(b.get("input", {}))) for b in blocks)
        if not stream:
            return self._send_json(200, {
                "id": "msg_mock_%s" % uuid.uuid4().hex[:16], "type": "message",
                "role": "assistant", "model": model, "content": blocks,
                "stop_reason": stop_reason, "stop_sequence": None,
                "usage": {"input_tokens": in_tok, "output_tokens": out_tok}},
                self._limit_headers())
        self._stream(model, blocks, stop_reason, in_tok, out_tok, fault)

    # -- reply selection ------------------------------------------------
    def _reply(self, body):
        msgs = body.get("messages") or []
        tools = body.get("tools") or []
        tool_names = {t.get("name") for t in tools if isinstance(t, dict)}
        all_text = "\n".join(_text_of(m.get("content")) for m in msgs if isinstance(m, dict))
        first_user = next((_text_of(m.get("content")) for m in msgs
                           if isinstance(m, dict) and m.get("role") == "user"), "")
        assistant_turns = sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "assistant")
        if tools:
            for sc in self.state.scenarios:
                match = sc.get("match")
                if match and match not in first_user and match not in all_text:
                    continue
                steps = sc.get("steps") or []
                needed = {s["tool_use"]["name"] for s in steps if "tool_use" in s}
                if not needed <= tool_names:
                    continue
                if assistant_turns < len(steps):
                    step = steps[assistant_turns]
                else:
                    step = {"text": sc.get("final_text", "Done.")}
                if "tool_use" in step:
                    tu = step["tool_use"]
                    return [{"type": "tool_use", "id": "toolu_mock_%s" % uuid.uuid4().hex[:12],
                             "name": tu["name"], "input": tu.get("input", {})}], "tool_use"
                return [{"type": "text", "text": step.get("text", "Done.")}], "end_turn"
            choice = body.get("tool_choice") or {}
            asked = next((t for t in tools if isinstance(t, dict) and t.get("name") and
                          ("use the %s tool" % t["name"].lower()) in first_user.lower()), None)
            if assistant_turns == 0 and (choice.get("type") in ("any", "tool") or
                                         (choice.get("type", "auto") == "auto" and asked)):
                tool = asked or tools[0]
                if choice.get("type") == "tool":
                    tool = next((t for t in tools if t.get("name") == choice.get("name")), tool)
                return [{"type": "tool_use", "id": "toolu_mock_%s" % uuid.uuid4().hex[:12],
                         "name": tool.get("name"),
                         "input": _sample_value(tool.get("input_schema") or {})}], "tool_use"
        last = _text_of(msgs[-1].get("content")) if msgs and isinstance(msgs[-1], dict) else ""
        if "count from 1 to" in last.lower():
            text = " ".join(str(i) for i in range(1, 41))
        elif "single word ready" in last.lower():
            text = "READY"
        else:
            text = "OK. This is the mock gateway."
        return [{"type": "text", "text": text}], "end_turn"

    # -- streaming ------------------------------------------------------
    def _events(self, model, blocks, stop_reason, in_tok, out_tok):
        msg_id = "msg_mock_%s" % uuid.uuid4().hex[:16]
        yield "message_start", {"type": "message_start", "message": {
            "id": msg_id, "type": "message", "role": "assistant", "model": model, "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": in_tok, "output_tokens": 1}}}
        yield "ping", {"type": "ping"}
        for i, b in enumerate(blocks):
            if b["type"] == "text":
                yield "content_block_start", {"type": "content_block_start", "index": i,
                                              "content_block": {"type": "text", "text": ""}}
                words = b["text"].split(" ")
                for j, w in enumerate(words):
                    piece = w if j == 0 else " " + w
                    yield "content_block_delta", {"type": "content_block_delta", "index": i,
                                                  "delta": {"type": "text_delta", "text": piece}}
            else:
                yield "content_block_start", {"type": "content_block_start", "index": i,
                                              "content_block": {"type": "tool_use", "id": b["id"],
                                                                "name": b["name"], "input": {}}}
                js = json.dumps(b["input"])
                for k in range(0, len(js), 12):
                    yield "content_block_delta", {"type": "content_block_delta", "index": i,
                                                  "delta": {"type": "input_json_delta",
                                                            "partial_json": js[k:k + 12]}}
            yield "content_block_stop", {"type": "content_block_stop", "index": i}
        yield "message_delta", {"type": "message_delta",
                                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                "usage": {"output_tokens": out_tok}}
        yield "message_stop", {"type": "message_stop"}

    def _stream(self, model, blocks, stop_reason, in_tok, out_tok, fault):
        events = list(self._events(model, blocks, stop_reason, in_tok, out_tok))
        frames = [("event: %s\ndata: %s\n\n" % (n, json.dumps(d))).encode("utf-8")
                  for n, d in events]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        for k, v in self._limit_headers().items():
            self.send_header(k, v)
        if fault == "cached-replay":
            # The gateway's response cache replays a stream: a ping first, then
            # every event at once.
            self.send_header("x-litellm-cache-key", "litellm.caching.caching:mock")
            frames = [b'event: ping\ndata: {"type": "ping"}\n\n'] + \
                [f for f, (n, _) in zip(frames, events) if n != "ping"]
            fault = "buffered-stream"
        if fault == "buffered-stream":
            body = b"".join(frames)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        if fault == "dropped-stream":
            # Stop after the first content delta: no message_delta, no message_stop.
            cut = next((i for i, (n, _) in enumerate(events) if n == "content_block_delta"), 2) + 1
            frames = frames[:cut]
        try:
            for f in frames:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(f), f))
                self.wfile.flush()
                if self.state.event_delay:
                    time.sleep(self.state.event_delay)
            if fault == "dropped-stream":
                self.close_connection = True
                try:
                    self.connection.shutdown(2)
                except OSError:
                    pass
                return
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


class MockServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        """A client that hangs up early (slow-first-byte, timeouts) is normal
        here; do not print a traceback for it."""
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError,
                                          ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def make_server(host="127.0.0.1", port=0, **state_kwargs):
    state = MockState(**state_kwargs)
    handler = type("BoundHandler", (Handler,), {"state": state})
    server = MockServer((host, port), handler)
    server.daemon_threads = True
    server.state = state
    return server


def start_in_thread(**kwargs):
    """For tests: returns (server, base_url). Call server.shutdown() when done."""
    server = make_server(**kwargs)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = server.server_address[:2]
    return server, "http://%s:%d" % (host, port)


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Mock LiteLLM/Anthropic gateway for dfir-lab tests.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (default 127.0.0.1; keep it local)")
    ap.add_argument("--port", type=int, default=8765, help="port (0 = any free port)")
    keys = ap.add_mutually_exclusive_group()
    keys.add_argument("--key-file", help="file holding the one accepted key")
    keys.add_argument("--key-env", default="MOCK_GATEWAY_KEY",
                      help="environment variable holding the key (default MOCK_GATEWAY_KEY)")
    ap.add_argument("--models", default="mock-main,mock-small",
                    help="comma-separated model IDs served")
    ap.add_argument("--scenario", action="append",
                    help="scenario JSON file (repeatable; default: scenarios/*.json)")
    ap.add_argument("--fault", default="none", choices=FAULTS)
    ap.add_argument("--slow-seconds", type=float, default=5.0,
                    help="delay used by the slow-first-byte fault")
    ap.add_argument("--event-delay", type=float, default=0.02,
                    help="seconds between streamed events")
    ap.add_argument("--log", help="request log file (JSON Lines)")
    ap.add_argument("--port-file", help="write the chosen port to this file")
    args = ap.parse_args(argv)
    if args.key_file:
        with open(args.key_file, encoding="utf-8") as fh:
            key = fh.read().strip()
    else:
        key = os.environ.get(args.key_env, "")
    if not key:
        ap.error("no key: use --key-file FILE or set %s" % args.key_env)
    scen = args.scenario
    if scen is None:
        sdir = os.path.join(here, "scenarios")
        scen = [os.path.join(sdir, f) for f in sorted(os.listdir(sdir)) if f.endswith(".json")] \
            if os.path.isdir(sdir) else []
    server = make_server(args.host, args.port, key=key,
                         models=[m.strip() for m in args.models.split(",") if m.strip()],
                         scenarios=load_scenarios(scen), fault=args.fault,
                         slow_seconds=args.slow_seconds, log_path=args.log,
                         event_delay=args.event_delay)
    port = server.server_address[1]
    if args.port_file:
        with open(args.port_file, "w") as fh:
            fh.write(str(port))
    sys.stdout.write("mock gateway listening on http://%s:%d (fault: %s)\n"
                     % (args.host, port, args.fault))
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
