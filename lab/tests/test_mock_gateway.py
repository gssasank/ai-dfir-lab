"""The mock gateway itself: request-log redaction and quiet client disconnects."""

import contextlib
import io
import json
import socket
import time
import unittest

import helpers
import mock_gateway
from dfir_lab.gateway import Gateway


class MockLogRedactionTests(helpers.LabTestCase):
    def test_key_in_request_fields_is_not_logged(self):
        # Review L3 (P1, exactly): the key sent as the model name was logged verbatim.
        key = 'sk-Review"AbCd\\Ef0123456789+/'
        self.server.state.key = key
        Gateway(self.base_url, key).messages({"model": key, "max_tokens": 1, "messages": []})
        Gateway(self.base_url, key).request("GET", "/v1/models?probe=" + key)
        text = self.mock_log.read_text()
        self.assertNotIn(key, text)
        self.assertNotIn(json.dumps(key)[1:-1], text)
        self.assertIn(mock_gateway.MARK, text)
        for line in text.splitlines():
            json.loads(line)                       # still valid JSON Lines


class _Raiser:
    def write(self, data):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        raise BrokenPipeError(32, "Broken pipe")


class QuietDisconnectTests(unittest.TestCase):
    def test_send_json_survives_broken_pipe(self):
        # The review's suite output showed a BrokenPipeError traceback from
        # _send_json when the client had already given up.
        server = mock_gateway.make_server(key="sk-test-12345678", models=["m"])
        try:
            h = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
            h.wfile = _Raiser()
            h.request_version = "HTTP/1.1"
            h.requestline = "POST /v1/messages HTTP/1.1"
            h.command = "POST"
            h.client_address = ("127.0.0.1", 1)
            h.close_connection = False
            h._send_json(200, {"ok": True})        # must not raise
            self.assertTrue(h.close_connection)
        finally:
            server.server_close()

    def test_server_does_not_print_disconnect_tracebacks(self):
        server = mock_gateway.make_server(key="sk-test-12345678", models=["m"])
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                try:
                    raise BrokenPipeError(32, "Broken pipe")
                except BrokenPipeError:
                    server.handle_error(None, ("127.0.0.1", 1))
        finally:
            server.server_close()
        self.assertEqual(err.getvalue(), "")

    def test_slow_first_byte_client_timeout_is_quiet(self):
        server, base = mock_gateway.start_in_thread(key="sk-test-12345678", models=["m"],
                                                    fault="slow-first-byte", slow_seconds=0.6)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                host, port = server.server_address[:2]
                body = json.dumps({"model": "m", "max_tokens": 1,
                                   "messages": [{"role": "user", "content": "x"}]}).encode()
                for _ in range(3):
                    s = socket.create_connection((host, port))
                    s.sendall(b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nx-api-key: "
                              b"sk-test-12345678\r\ncontent-type: application/json\r\n"
                              b"content-length: %d\r\n\r\n%s" % (len(body), body))
                    s.close()                       # give up before the reply
                time.sleep(1.5)
        finally:
            server.shutdown()
            server.server_close()
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
