#!/usr/bin/env python3
"""Metering reverse proxy for dry runs: 127.0.0.1:PORT -> UPSTREAM.

Forwards every request unchanged (except Accept-Encoding, so responses come
back uncompressed and their usage can be read) and streams the response back.
Appends one JSON line per request to LOG: time, method, path, status, timing,
token usage from the SSE stream (message_start / message_delta), the retry
count Claude Code sends, and the error type of a failed request.
Header VALUES are never logged, so the key never reaches the log.

    meter-proxy.py PORT UPSTREAM LOG
"""
import http.client
import json
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

PORT, UPSTREAM, LOG = int(sys.argv[1]), sys.argv[2], sys.argv[3]
U = urlsplit(UPSTREAM)
LOCK = threading.Lock()
SEQ = [0]
HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "upgrade", "te",
       "trailer", "accept-encoding", "host", "content-length"}


def record(rec):
    with LOCK:
        SEQ[0] += 1
        rec["seq"] = SEQ[0]
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _proxy(self):
        t0 = time.time()
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["Host"] = U.netloc
        if body is not None:
            headers["Content-Length"] = str(len(body))
        rec = {"t_start": round(t0, 3), "method": self.command, "path": self.path,
               "retry_count": self.headers.get("x-stainless-retry-count"),
               "req_bytes": len(body or b"")}
        try:
            if body and self.path.startswith("/v1/messages"):
                req = json.loads(body)
                rec["model"] = req.get("model")
                rec["n_messages"] = len(req.get("messages") or [])
                rec["n_tools"] = len(req.get("tools") or [])
        except ValueError:
            pass
        conn = http.client.HTTPSConnection(U.hostname, U.port or 443, timeout=300,
                                           context=ssl.create_default_context())
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except Exception as exc:  # noqa: BLE001
            rec.update(status=599, error=exc.__class__.__name__, t_total=round(time.time() - t0, 3))
            record(rec)
            self.send_error(502)
            return
        rec["status"] = resp.status
        rec["t_headers"] = round(time.time() - t0, 3)
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        usage, buf, err = {}, b"", b""
        while True:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            if not chunk:
                break
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except OSError:
                break
            if resp.status != 200:
                err += chunk[:4000]
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.startswith(b"data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if ev.get("type") == "message_start":
                    usage.update((ev.get("message") or {}).get("usage") or {})
                elif ev.get("type") == "message_delta":
                    usage.update({k: v for k, v in (ev.get("usage") or {}).items() if v is not None})
                    rec["stop_reason"] = (ev.get("delta") or {}).get("stop_reason")
                elif "usage" in ev and isinstance(ev["usage"], dict):     # non-streaming body
                    usage.update(ev["usage"])
        if resp.status == 200 and not usage and buf:
            try:
                usage = (json.loads(buf) or {}).get("usage") or {}
            except ValueError:
                pass
        rec["t_total"] = round(time.time() - t0, 3)
        rec["usage"] = {k: usage.get(k) for k in ("input_tokens", "cache_creation_input_tokens",
                                                  "cache_read_input_tokens", "output_tokens")
                        if k in usage}
        if err:
            try:
                e = json.loads(err)
                rec["error"] = (e.get("error") or {}).get("type")
                msg = (e.get("error") or {}).get("message") or ""
                rec["error_message"] = msg[:300]
            except ValueError:
                rec["error"] = "unparsed"
        conn.close()
        record(rec)
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _proxy


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
