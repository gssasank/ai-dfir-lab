"""Every `check` outcome against the mock gateway, including each fault mode
mapping to the right student-facing message."""

import json
import socket
import unittest
from unittest import mock

import helpers
from dfir_lab import check, gateway, readiness, redact
from dfir_lab.config import Config
from dfir_lab.gateway import HttpResult, classify

CANNOT_REACH = "Cannot reach the gateway"
REJECTED = "rejected your key"


def run(cfg, ids):
    return check.run_checks(cfg, ids).results


class CheckGatewayTests(helpers.LabTestCase):
    def setUp(self):
        super().setUp()
        self.store_key()
        self.cfg = Config()

    def assertStatus(self, results, cid, status):
        r = results[cid]
        self.assertEqual(r.status, status, "check %d: %s / %s" % (cid, r.result, r.todo))
        return r

    def test_normal_all_gateway_checks_pass(self):
        res = run(self.cfg, list(range(1, 12)))
        for cid in range(1, 12):
            self.assertStatus(res, cid, "PASS")
        self.assertIn("mock-main", res[6].result)
        self.assertIn("message_start to message_stop", res[8].result)

    def test_wrong_key(self):
        self.set_fault("wrong-key")
        res = run(self.cfg, [4, 5, 6, 7, 8])
        self.assertStatus(res, 5, "PASS")
        r = self.assertStatus(res, 6, "FAIL")
        self.assertIn(REJECTED, r.result)
        self.assertNotIn(CANNOT_REACH, r.result)
        self.assertIn("setup-key", r.todo)
        self.assertNotIn(self.mock_key, r.result)          # LiteLLM echoes only the last 4
        self.assertStatus(res, 7, "SKIP")
        self.assertIn("check 6", res[7].result)
        self.assertStatus(res, 8, "SKIP")

    def test_really_wrong_key_without_fault(self):
        self.store_key(helpers.random_key())
        res = run(self.cfg, [4, 5, 6])
        self.assertIn(REJECTED, self.assertStatus(res, 6, "FAIL").result)

    def test_forbidden_model(self):
        self.set_fault("forbidden-model")
        res = run(self.cfg, [4, 5, 6, 7])
        r6 = self.assertStatus(res, 6, "WARN")
        self.assertIn("not in the list", r6.result)
        r7 = self.assertStatus(res, 7, "FAIL")
        self.assertIn("not allowed to use the course model mock-main", r7.result)
        self.assertNotIn(REJECTED, r7.result)
        self.assertIn("the TA (test)", r7.todo)

    def test_unknown_model_404_and_400(self):
        for fault in ("unknown-model-404", "unknown-model-400"):
            self.set_fault(fault)
            res = run(self.cfg, [4, 5, 6, 7])
            r7 = self.assertStatus(res, 7, "FAIL")
            self.assertIn("does not offer the course model mock-main", r7.result, fault)
            self.assertNotIn("no Anthropic Messages route", r7.result)

    def test_model_missing_from_list_names_similar(self):
        self.write_conf({("gateway", "model_main"): "mock-mian"})
        cfg = Config()
        res = run(cfg, [4, 5, 6])
        r6 = self.assertStatus(res, 6, "WARN")
        self.assertIn("mock-mian", r6.result)
        self.assertIn("mock-main", r6.result)               # similar model named

    def test_rate_limit(self):
        self.set_fault("rate-limit")
        res = run(self.cfg, [4, 5, 6])
        r6 = self.assertStatus(res, 6, "FAIL")
        self.assertIn("usage limit", r6.result)
        self.assertIn("3600", r6.result)                     # retry-after shown

    def test_server_error(self):
        self.set_fault("server-error")
        res = run(self.cfg, [4, 5, 6])
        r6 = self.assertStatus(res, 6, "FAIL")
        self.assertIn("internal error (HTTP 500)", r6.result)
        self.assertIn("not your fault", r6.result)

    def test_slow_first_byte(self):
        self.write_conf({("gateway", "request_timeout_s"): "1"})
        self.set_fault("slow-first-byte")
        res = run(Config(), [4, 5, 6, 7])
        self.assertStatus(res, 6, "PASS")
        r7 = self.assertStatus(res, 7, "FAIL")
        self.assertIn("did not answer within 1 seconds", r7.result)
        self.assertNotIn(CANNOT_REACH, r7.result)

    def test_buffered_stream(self):
        self.set_fault("buffered-stream")
        res = run(self.cfg, [4, 5, 6, 7, 8])
        self.assertStatus(res, 7, "PASS")
        r8 = self.assertStatus(res, 8, "FAIL")
        self.assertIn("all at once", r8.result)

    def test_dropped_stream(self):
        self.set_fault("dropped-stream")
        res = run(self.cfg, [4, 5, 6, 7, 8])
        r8 = self.assertStatus(res, 8, "FAIL")
        self.assertIn("cut off", r8.result)

    def test_no_anthropic_route(self):
        self.set_fault("no-anthropic-route")
        res = run(self.cfg, [4, 5, 6, 7])
        self.assertStatus(res, 6, "PASS")
        r7 = self.assertStatus(res, 7, "FAIL")
        self.assertIn("no Anthropic Messages route", r7.result)
        self.assertIn("dfir-lab replay", r7.todo)

    def test_tool_use_bad_reply(self):
        # A gateway that ignores tools: reply is plain text.
        with mock.patch.object(gateway.Gateway, "messages", return_value=HttpResult(
                status=200, body=json.dumps({"type": "message", "content": [
                    {"type": "text", "text": "12:00"}], "stop_reason": "end_turn"}).encode())):
            res = run(self.cfg, [9])
        r9 = self.assertStatus(res, 9, "FAIL")
        self.assertIn("did not call the tool", r9.result)

    def test_check10_fake_claude_success_and_failure(self):
        res = run(self.cfg, [10])
        self.assertStatus(res, 10, "PASS")
        log = self.fake_log.read_text()
        self.assertIn("ARG=--max-turns", log)
        self.assertIn("ARG=Bash(sh readiness.sh)", log)
        self.assertIn("ENV=ANTHROPIC_BASE_URL=%s" % self.base_url, log)
        self.assertNotIn(self.mock_key, log)                 # never in argv or env
        self.assertTrue((self.claude_dir / "settings.json").is_file())  # settings ensured
        with mock.patch.dict("os.environ", {"FAKE_CLAUDE_MODE": "fail"}):
            res = run(self.cfg, [10])
        r = self.assertStatus(res, 10, "FAIL")
        self.assertIn("turn limit", r.result)

    def test_check10_missing_claude(self):
        self.write_conf({("claude", "binary"): "/nonexistent/claude"})
        res = run(Config(), [10])
        self.assertIn("not installed", self.assertStatus(res, 10, "FAIL").result)

    def test_clock_skew_warning(self):
        ctx = check.run_checks(self.cfg, [1])
        ctx.date_header = "Mon, 01 Jan 2024 00:00:00 GMT"
        check.amend_clock(ctx)
        self.assertEqual(ctx.results[1].status, "WARN")
        self.assertIn("timedatectl", ctx.results[1].todo)

    # -- review M1: new mock faults in LiteLLM's current envelope ------------
    def test_unsupported_param_is_its_own_outcome(self):
        self.set_fault("unsupported-param")
        r7 = self.assertStatus(run(self.cfg, [7]), 7, "FAIL")
        self.assertIn("rejected part of the request", r7.result)
        self.assertNotIn("does not offer the course model", r7.result)
        self.assertIn("tool_choice", r7.result)               # the gateway's words are shown

    def test_no_healthy_deployments_and_cooldown_are_busy(self):
        for fault in ("no-healthy-deployments", "cooldown"):
            self.set_fault(fault)
            r7 = self.assertStatus(run(self.cfg, [7]), 7, "FAIL")
            self.assertIn("busy or temporarily down", r7.result, fault)
            self.assertNotIn("does not offer", r7.result, fault)
            self.assertNotIn("reached its usage limit", r7.result, fault)
            self.assertIn("try again", r7.todo.lower(), fault)

    def test_upstream_401_reported_with_uncertainty(self):
        self.set_fault("upstream-auth")
        res = run(self.cfg, [4, 5, 6, 7])
        self.assertStatus(res, 6, "PASS")
        r7 = self.assertStatus(res, 7, "FAIL")
        self.assertIn("cannot tell this for certain", r7.result)
        self.assertIn("HTTP 401", r7.result)
        self.assertNotIn(REJECTED, r7.result)

    def test_mock_uses_current_envelope(self):
        gw = gateway.Gateway(self.base_url, self.mock_key)
        self.set_fault("forbidden-model")
        body = gw.messages({"model": "mock-main", "max_tokens": 1,
                            "messages": [{"role": "user", "content": "x"}]}).json()
        self.assertEqual(body["type"], "error")
        self.assertEqual(body["error"]["type"], "permission_error")
        self.assertEqual(body["error"]["litellm_error_type"], "key_model_access_denied")

    # -- review M2: a 200 is not automatically a pass -------------------------
    def test_html_200_login_page_is_not_a_pass(self):
        # Review P5, exactly: a real local HTTP server answering 200 with HTML.
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        class Portal(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                b = b"<html>Please log into captive portal</html>"
                self.send_response(200)
                self.send_header("content-length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Portal)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.write_conf({("gateway", "base_url"): "http://127.0.0.1:%d" % srv.server_port})
            r = check.check_login(check.Ctx(Config()))
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(r.status, "FAIL", r.result)
        self.assertNotIn("key accepted", r.result)
        self.assertIn("login", r.result)
        self.assertIn("captive portal", r.result)

    def test_unknown_json_model_list_is_warn(self):
        with mock.patch.object(gateway.Gateway, "models", return_value=HttpResult(
                status=200, body=b'{"object": "list"}')):
            r = run(self.cfg, [6])[6]
        self.assertEqual(r.status, "WARN", r.result)

    def test_message_without_content_fails(self):
        # Review P1, exactly: {"type":"message","content":[]}.
        for body in (b'{"type":"message","content":[]}',
                     b'{"type":"message","content":[{"type":"text","text":"  "}]}'):
            with mock.patch.object(gateway.Gateway, "messages",
                                   return_value=HttpResult(status=200, body=body)):
                r = run(self.cfg, [7])[7]
            self.assertEqual(r.status, "FAIL", body)
            self.assertIn("no text", r.result)

    def test_invalid_stream_events_fail(self):
        # Review P1, exactly: message_start and message_stop, both without valid JSON.
        sr = gateway.StreamResult(status=200, events=[("message_start", None, 0),
                                                      ("message_stop", None, 0)],
                                  complete=True, t_first=0, t_stop=0)
        with mock.patch.object(gateway.Gateway, "stream_messages", return_value=sr):
            r = run(self.cfg, [8])[8]
        self.assertEqual(r.status, "FAIL", r.result)
        self.assertIn("not valid JSON", r.result)

    def test_stream_without_text_fails(self):
        sr = gateway.StreamResult(status=200, events=[
            ("message_start", {"type": "message_start", "message": {}}, 0),
            ("message_stop", {"type": "message_stop"}, 0.2)], complete=True, t_first=0, t_stop=.2)
        with mock.patch.object(gateway.Gateway, "stream_messages", return_value=sr):
            r = run(self.cfg, [8])[8]
        self.assertEqual(r.status, "FAIL", r.result)

    def test_short_stream_is_inconclusive_warn(self):
        def delta(t):
            return ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                            "delta": {"type": "text_delta", "text": t}}, .1)
        sr = gateway.StreamResult(status=200, events=[
            ("message_start", {"type": "message_start", "message": {}}, 0), delta("OK"),
            ("message_stop", {"type": "message_stop"}, 0.2)], complete=True, t_first=0, t_stop=.2)
        with mock.patch.object(gateway.Gateway, "stream_messages", return_value=sr):
            r = run(self.cfg, [8])[8]
        self.assertEqual(r.status, "WARN", r.result)
        self.assertIn("too short", r.result)

    # -- review H6 / spec 6.4 for check 10 ------------------------------------
    def test_check10_stops_when_settings_cannot_be_installed(self):
        self.claude_dir.mkdir()
        (self.claude_dir / "settings.json").write_text("{invalid")
        r = self.assertStatus(run(self.cfg, [10]), 10, "FAIL")
        self.assertIn("not valid JSON", r.result)
        self.assertIn("Claude Code was not run", r.result)
        self.assertFalse(self.fake_log.exists())

    def test_check10_refuses_old_claude(self):
        with mock.patch.dict("os.environ", {"FAKE_CLAUDE_VERSION": "2.1.200"}):
            r = self.assertStatus(run(self.cfg, [10]), 10, "FAIL")
        self.assertIn("2.1.200", r.result)
        self.assertIn("2.1.295", r.result)
        self.assertFalse(self.fake_log.exists())


class NetworkFailureTests(helpers.LabTestCase):
    start_mock = False   # nothing listens on base_url

    def setUp(self):
        super().setUp()
        self.store_key()

    def test_refused(self):
        res = run(Config(), [4, 5, 6, 7])
        r5 = res[5]
        self.assertEqual(r5.status, "FAIL")
        self.assertIn(CANNOT_REACH, r5.result)
        self.assertIn("refused", r5.result)
        self.assertNotIn(REJECTED, r5.result)
        self.assertIn("VPN (test hint)", r5.todo)
        self.assertEqual(res[6].status, "SKIP")

    def test_dns_failure(self):
        self.write_conf({("gateway", "base_url"): "https://no-such-host.invalid"})
        r5 = run(Config(), [5])[5]
        self.assertEqual(r5.status, "FAIL")
        self.assertIn("could not look up the name no-such-host.invalid", r5.result)

    def test_timeout(self):
        with mock.patch("socket.socket.connect", side_effect=socket.timeout("timed out")):
            r5 = run(Config(), [5])[5]
        self.assertEqual(r5.status, "FAIL")
        self.assertIn("did not answer within 2 seconds", r5.result)
        self.assertIn(CANNOT_REACH, r5.result)

    def test_certificate_error(self):
        import ssl
        err = ssl.SSLCertVerificationError(1, "certificate verify failed")
        err.verify_message = "certificate has expired"
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            with mock.patch("ssl.SSLContext.wrap_socket", side_effect=err):
                net = gateway.probe_network("https://127.0.0.1:%d" % port, 2)
        finally:
            srv.close()
        self.assertEqual(net.kind, "cert")
        from dfir_lab.messages import render
        what, todo = render("cert", Config(), host="127.0.0.1", detail=net.detail)
        self.assertIn("certificate check (certificate has expired)", what)
        self.assertIn("date and time", todo)

    # -- review M3: every resolved address within one deadline ----------------
    def test_second_address_is_tried(self):
        # Review P1, exactly: 127.0.0.2 (refused) first, a listening 127.0.0.1 second.
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen()
        port = srv.getsockname()[1]
        addr = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.2", port)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        try:
            with mock.patch("dfir_lab.gateway._resolve", return_value=addr):
                net = gateway.probe_network("http://localhost:%d" % port, 2)
        finally:
            srv.close()
        self.assertTrue(net.ok, net)
        self.assertEqual(net.address, "127.0.0.1")

    def test_all_addresses_fail_within_one_deadline(self):
        addr = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.%d" % i, 443))
                for i in range(1, 4)]
        tried = []

        def slow_connect(sock, sockaddr):
            tried.append(sockaddr[0])
            import time
            time.sleep(sock.gettimeout())
            raise socket.timeout("timed out")
        with mock.patch("dfir_lab.gateway._resolve", return_value=addr), \
                mock.patch("socket.socket.connect", slow_connect):
            import time
            t = time.monotonic()
            net = gateway.probe_network("https://gw.example", 1.5)
            elapsed = time.monotonic() - t
        self.assertFalse(net.ok)
        self.assertEqual(net.kind, "timeout")
        self.assertEqual(tried, ["192.0.2.1", "192.0.2.2", "192.0.2.3"])
        self.assertLess(elapsed, 2.2)
        self.assertIn("192.0.2.3", net.detail)

    def test_messages_are_distinct(self):
        from dfir_lab.messages import CANNOT_REACH as KINDS, OUTCOMES, render
        cfg = Config()
        whats = {k: render(k, cfg, status=401)[0] for k in OUTCOMES}
        self.assertEqual(len(set(whats.values())), len(whats))
        for k in KINDS:
            self.assertNotIn(REJECTED, whats[k])
        self.assertNotIn(CANNOT_REACH, whats["key_rejected"])


class ClassifyTests(unittest.TestCase):
    def r(self, status, body, headers=None):
        return HttpResult(status=status, body=json.dumps(body).encode() if not
                          isinstance(body, bytes) else body, headers=headers or {})

    def test_litellm_401_model_access_is_not_key_rejected(self):
        res = self.r(401, {"error": {"message": "Authentication Error - key not allowed to "
                                                "access model. This key can only access "
                                                "models=['a']. Tried to access b"}})
        self.assertEqual(classify(res, "b").kind, "model_forbidden")

    def test_plain_401_and_403(self):
        for s in (401, 403):
            self.assertEqual(classify(self.r(s, {"error": {"message": "bad token"}}), "m").kind,
                             "key_rejected")

    def test_budget(self):
        res = self.r(400, {"error": {"message": "Budget has been exceeded! Current cost: 5"}})
        self.assertEqual(classify(res, "m").kind, "limit")

    def test_models_404_is_warning(self):
        self.assertEqual(classify(self.r(404, {"detail": "Not Found"}), "m", "models").kind,
                         "models_404")

    def test_404_html_is_no_route(self):
        self.assertEqual(classify(self.r(404, b"<html>Not Found</html>"), "m").kind,
                         "no_anthropic_route")

    def test_other(self):
        self.assertEqual(classify(self.r(418, {"error": {"message": "teapot"}}), "m").kind,
                         "other")

    # -- review M1, current LiteLLM formats ------------------------------------
    def envelope(self, status, etype, message, litellm_type=None, headers=None):
        err = {"type": etype, "message": message}
        if litellm_type:
            err["litellm_error_type"] = litellm_type
        return self.r(status, {"type": "error", "error": err}, headers)

    def test_every_model_denial_layer_is_model_not_allowed(self):
        # Review P4, exactly: current generic message plus a typed denial code.
        msg = ("The requested model 'mock-main' is not available for this API key, or the "
               "model name is invalid.")
        for layer in ("key", "team", "user", "org", "project", "customer", "agent"):
            res = self.envelope(403, "permission_error", msg, layer + "_model_access_denied")
            self.assertEqual(classify(res, "mock-main").kind, "model_forbidden", layer)
        # The type alone is enough, whatever the message says.
        res = self.envelope(403, "permission_error", "Forbidden", "team_model_access_denied")
        self.assertEqual(classify(res, "m").kind, "model_forbidden")

    def test_review_p1_bodies(self):
        cases = [
            (400, {"error": {"type": "invalid_request_error",
                             "message": "mock-main does not support parameter tool_choice"}},
             "request_rejected"),
            (400, {"error": {"message": "There are no healthy deployments for this model mock-main"}},
             "busy"),
            (429, {"error": {"message": "No deployments available for selected model mock-main"}},
             "busy"),
            (401, {"error": {"message": "AnthropicException - invalid x-api-key"}}, "upstream_auth"),
            (403, {"error": {"message": "Not allowed to access model due to tags configuration"}},
             "model_forbidden"),
            (404, {"type": "error", "error": {"type": "not_found_error", "message": "Not Found"}},
             "unclear_404"),
            (404, {"error": {"message": "Requested route does not exist", "type": "not_found_error"}},
             "unclear_404"),
        ]
        for status, body, want in cases:
            self.assertEqual(classify(self.r(status, body), "mock-main").kind, want, body)

    def test_unsupported_field_echoing_model_is_not_unknown_model(self):
        for msg in ("litellm.UnsupportedParamsError: mock-main does not support parameters: "
                    "['thinking']", "Extra inputs are not permitted: context_management",
                    "mock-main: the field 'output_config' is not supported"):
            res = self.envelope(400, "invalid_request_error", msg)
            self.assertEqual(classify(res, "mock-main").kind, "request_rejected", msg)
        res = self.envelope(400, "invalid_request_error",
                            "/v1/messages: Invalid model name passed in model=mock-x")
        self.assertEqual(classify(res, "mock-x").kind, "model_unknown")

    def test_429_scopes(self):
        gateway_limit = self.envelope(429, "rate_limit_error", "Rate limit exceeded for api_key: "
                                      "sk-...abcd. Limit type: requests.", "throttling_error",
                                      {"retry-after": "30"})
        self.assertEqual(classify(gateway_limit, "m").kind, "limit")
        self.assertEqual(classify(gateway_limit, "m").retry_after, "30")
        budget = self.envelope(429, "rate_limit_error", "Budget has been exceeded! Key=x "
                               "Current cost: 6, Max budget: 5", "budget_exceeded")
        self.assertEqual(classify(budget, "m").kind, "limit")
        provider = self.r(429, {"error": {"message": "litellm.RateLimitError: RateLimitError: "
                                                     "AnthropicException - overloaded"}})
        self.assertEqual(classify(provider, "m").kind, "busy")
        cooldown = self.envelope(429, "rate_limit_error", "No deployments available for selected "
                                 "model, Try again in 60 seconds.", None, {"retry-after": "60"})
        self.assertEqual(classify(cooldown, "m").kind, "busy")
        for s in (503, 529):
            self.assertEqual(classify(self.r(s, {"error": {"message": "x"}}), "m").kind, "busy")

    def test_gateway_auth_types_are_key_rejected(self):
        for t in ("token_not_found_in_db", "expired_key", "auth_error"):
            res = self.envelope(401, "authentication_error", "litellm.AuthenticationError: "
                                "AnthropicException-looking text", t)
            self.assertEqual(classify(res, "m").kind, "key_rejected", t)
        res = self.envelope(401, "authentication_error", "Authentication Error, Invalid proxy "
                            "server token passed. Received API Key = sk-...abcd")
        self.assertEqual(classify(res, "m").kind, "key_rejected")

    def test_other_permission_is_not_key_rejected(self):
        res = self.envelope(403, "permission_error", "tool not allowed", "tool_access_denied")
        self.assertEqual(classify(res, "m").kind, "other")

    def test_404_with_model_wording(self):
        res = self.envelope(404, "not_found_error", "model: claude-nope")
        self.assertEqual(classify(res, "claude-nope").kind, "model_unknown")
        self.assertEqual(classify(self.r(404, {"detail": "Not Found"}), "m").kind,
                         "no_anthropic_route")

    def test_unclear_message_shows_status_and_redacted_body(self):
        from dfir_lab.messages import render
        key = helpers.random_key()
        redact.reset(key)
        try:
            out = classify(self.envelope(404, "not_found_error", "Not Found here " + key),
                           "mock-main")
            what, todo = render(out.kind, None, **{"status": out.status, "body": out.detail,
                                                   "model": "mock-main"})
        finally:
            redact.reset()
        self.assertIn("HTTP 404", what)
        self.assertIn("cannot tell", what)
        self.assertIn("Not Found here [REDACTED-KEY]", what)
        self.assertNotIn(key, what)


class CheckCliTests(helpers.LabTestCase):
    def test_full_check_ready_report_and_code(self):
        self.store_key()
        r = self.run_cli("check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("READY - every check passed", r.stdout)
        self.assertIn("Readiness code: DFIR-0.1.0-test-R-", r.stdout)
        self.assertIn("Tokens used:", r.stdout)
        rdir = self.home / "dfir-lab" / "readiness"
        latest = (rdir / "latest.txt").read_text()
        self.assertIn("READY", latest)
        self.assertIn("does not contain your Duke AI key", latest)
        doc = helpers.read_json(rdir / "latest.json")
        ok, _ = readiness.verify(doc["code"], doc)
        self.assertTrue(ok)
        self.assertGreater(doc["tokens"]["total"], 0)
        self.assertEqual(len(list(rdir.glob("readiness-*.txt"))), 1)

    def test_quick_json_and_only(self):
        self.store_key()
        r = self.run_cli("check", "--quick", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual([x["id"] for x in doc["results"]], [1, 2, 3, 4, 5])
        self.assertFalse(doc["ready"])
        self.assertIn("-N-", doc["code"])
        r = self.run_cli("check", "--only", "7,8", "--json")
        doc = json.loads(r.stdout)
        self.assertEqual([x["id"] for x in doc["results"]], [7, 8])

    def test_json_stays_pure_with_clock_warning(self):
        # Review M5 (P1, exactly): a clock-amended check 1 broke --json output.
        import io
        from contextlib import redirect_stdout
        cfg = Config()
        ctx = check.Ctx(cfg)
        ctx.results = {1: check.Result(1, "System", "WARN", "clock skew", "fix",
                                       {"clock_amended": True})}
        buf = io.StringIO()
        with mock.patch("dfir_lab.check.run_checks", return_value=ctx), redirect_stdout(buf):
            check.run(cfg, as_json=True)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["results"][0]["result"], "clock skew")

    def test_not_ready_exit_1(self):
        r = self.run_cli("check")               # no key stored
        self.assertEqual(r.returncode, 1)
        self.assertIn("NOT READY", r.stdout)
        self.assertIn("No Duke AI key is stored", r.stdout)
        self.assertIn("dfir-lab setup-key", r.stdout)

    def test_evidence_changed_fails_case_check(self):
        ev = self.case / "evidence" / "security-events.csv"
        import os
        os.chmod(ev.parent, 0o755)
        os.chmod(ev, 0o644)
        with open(ev, "a") as fh:
            fh.write("9999,tampered\n")
        res = run(Config(), [3])
        self.assertEqual(res[3].status, "FAIL")
        self.assertIn("changed", res[3].result)

    def test_writable_evidence_warns(self):
        import os
        os.chmod(self.case / "evidence", 0o755)
        res = run(Config(), [3])
        self.assertEqual(res[3].status, "WARN")


class SetupKeyOutcomeTests(helpers.LabTestCase):
    def outcome(self, fault=None, key=None):
        if fault:
            self.set_fault(fault)
        r = self.store_key(key)
        return r.returncode, r.stdout + r.stderr

    def test_saved_and_working(self):
        code, out = self.outcome()
        self.assertEqual(code, 0, out)
        self.assertIn("saved and working", out)

    def test_wrong_key(self):
        code, out = self.outcome("wrong-key")
        self.assertEqual(code, 1)
        self.assertIn("gateway rejected it", out)
        self.assertIn("Duke AI dashboard", out)

    def test_model_unavailable(self):
        code, out = self.outcome("forbidden-model")
        self.assertIn("course model mock-main is not available", out)
        self.assertIn("the TA (test)", out)

    def test_limit(self):
        code, out = self.outcome("rate-limit")
        self.assertIn("reached its usage limit", out)

    def test_cooldown_is_not_a_daily_limit(self):
        # Review M1/P1: a deployment cooldown 429 made setup-key assert a daily limit.
        code, out = self.outcome("cooldown")
        self.assertEqual(code, 1)
        self.assertIn("busy or down", out)
        self.assertNotIn("reached its usage limit", out)

    def test_other(self):
        code, out = self.outcome("server-error")
        self.assertIn("unexpected error", out)
        self.assertIn("HTTP 500", out)

    def test_unreachable(self):
        self.server.shutdown()
        self.server.server_close()
        self.server = None
        code, out = self.outcome()
        self.assertEqual(code, 1)
        self.assertIn("cannot be reached", out)
        self.assertIn("VPN (test hint)", out)
        self.assertTrue((self.home / ".config" / "dfir-lab" / "duke-api-key").exists())


if __name__ == "__main__":
    unittest.main()
