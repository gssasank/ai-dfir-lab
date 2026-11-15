#!/usr/bin/env python3
"""One interactive lab session in a pseudo-terminal, to measure what /clear is
worth: two checkpoint questions back to back without /clear, then the same two
questions with /clear before each.

    clear-compare.py OUTDIR QA QB [--port 8899] [--log /var/tmp/dfir-dry/proxy.jsonl]

Starts `dfir-lab start` (the student's command) with Claude Code's requests
sent through meter-proxy.py, types each question as a student would (question
text from checkpoints.json plus "Show me the evidence and the command you
ran."), and waits until the proxy has seen the reply finish (stop_reason
end_turn, then 8 s without a new request). Writes OUTDIR/clear-compare.json
and OUTDIR/screen.txt.
"""
import argparse
import fcntl
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/dfir-lab")
from dfir_lab.config import Config  # noqa: E402

SUFFIX = " Show me the evidence and the command you ran."
PRICE = {"input_tokens": 1e-7, "output_tokens": 5e-7, "cache_read_input_tokens": 1e-8,
         "cache_creation_input_tokens": 1.25e-7}


def records(log):
    out = []
    try:
        for line in Path(log).read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def drain(fd, sink, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if not data:
                return
            sink.append(data)


def wait_reply(fd, sink, log, since, timeout=420):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        drain(fd, sink, 2)
        recs = [r for r in records(log) if r.get("t_start", 0) >= since
                and str(r.get("path", "")).startswith("/v1/messages")]
        done = [r for r in recs if r.get("status") == 200 and r.get("stop_reason") == "end_turn"]
        if done:
            last = max(r["t_start"] + (r.get("t_total") or 0) for r in recs)
            if time.time() - last > 8:
                return True, last
    return False, time.time()


def summarize(log, since, until):
    msgs = [r for r in records(log) if since <= r.get("t_start", 0) <= until
            and r.get("method") == "POST" and str(r.get("path", "")).startswith("/v1/messages")
            and "count_tokens" not in r.get("path", "")]
    ok = [r for r in msgs if r.get("status") == 200]
    rl = [r for r in msgs if r.get("status") == 429]
    wait = 0.0
    for r in rl:
        nxt = [m["t_start"] for m in msgs if m["t_start"] > r["t_start"]]
        if nxt:
            wait += max(0.0, min(nxt) - (r["t_start"] + (r.get("t_total") or 0)))
    tok = [sum((r.get("usage") or {}).get(k) or 0 for k in PRICE) for r in ok]
    cost = sum(((r.get("usage") or {}).get(k) or 0) * p * 0.5 for r in ok for k, p in PRICE.items())
    return {"requests": len(msgs), "rate_limited": len(rl), "wait_s": round(wait, 1),
            "tokens": sum(tok), "max_request_tokens": max(tok or [0]),
            "first_request_tokens": tok[0] if tok else 0, "cost_usd": round(cost, 5)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("qa")
    ap.add_argument("qb")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--log", default="/var/tmp/dfir-dry/proxy.jsonl")
    ap.add_argument("--chain", help="instead: ask these checkpoints (e.g. 3,8,4,5) in one "
                                    "conversation without /clear")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cps = {str(c["id"]): c for c in json.load(open(cfg.case_path("checkpoints")))["checkpoints"]}
    qa, qb = cps[a.qa]["question"] + SUFFIX, cps[a.qb]["question"] + SUFFIX
    plan = [("question", "A1", qa), ("question", "A2", qb),
            ("clear", None, None), ("question", "B1", qa),
            ("clear", None, None), ("question", "B2", qb)]
    if a.chain:
        plan = [("question", "C%d-cp%s" % (i, n), cps[n]["question"] + SUFFIX)
                for i, n in enumerate(a.chain.split(","), 1)]
    env = dict(os.environ, TERM="xterm-256color", COLUMNS="120", LINES="40")
    settings = json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % a.port}})
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    proc = subprocess.Popen(["dfir-lab", "start", "--", "--settings", settings], env=env,
                            stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                            cwd=str(Path.home()))
    os.close(slave)
    sink, results = [], []
    try:
        drain(master, sink, 25)
        for kind, label, text in plan:
            if kind == "clear":
                os.write(master, b"/clear")
                drain(master, sink, 1.5)
                os.write(master, b"\r")
                drain(master, sink, 4)
                results.append({"step": "/clear"})
                continue
            t_send = time.time()
            os.write(master, text.encode())
            drain(master, sink, 1.5)
            os.write(master, b"\r")
            ok, t_end = wait_reply(master, sink, a.log, t_send)
            s = summarize(a.log, t_send, t_end + 1)
            s.update(step=label, question=text, finished=ok, wall_s=round(t_end - t_send, 1))
            results.append(s)
            print(json.dumps(s), flush=True)
            drain(master, sink, 3)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait(timeout=30)
    text = b"".join(sink).decode("utf-8", "replace")
    text = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", " ", text)
    (out / "screen.txt").write_text(text)
    (out / "clear-compare.json").write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
