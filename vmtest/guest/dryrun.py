#!/usr/bin/env python3
"""Dry run of the lab's checkpoints, as a student would ask them.

For each checkpoint: a fresh non-interactive Claude Code session in the case
folder with the lab's real settings (~/.claude/settings.json plus the
environment `dfir-lab start` gives Claude Code), asked the checkpoint's
question from the installed checkpoints.json plus "Show me the evidence and
the command you ran." Requests go through meter-proxy.py (127.0.0.1:PORT) so
requests, tokens, 429s and backoff time are measured. The key's spend is read
from the gateway's /key/info (only the spend number is kept).

    dryrun.py OUTDIR 1,2,3 [--port 8899] [--gap 65] [--stop-spend 0.60]

Writes OUTDIR/cp<N>/{stream.jsonl,stderr.txt,result.txt,summary.json} and
OUTDIR/summary.jsonl. Never writes the key anywhere.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/dfir-lab")
from dfir_lab.claude_env import process_env  # noqa: E402
from dfir_lab.config import Config  # noqa: E402

# Haiku 5.5 list prices per token (gateway /model/info), charged at 50% by the gateway.
PRICE = {"input_tokens": 1e-7, "output_tokens": 5e-7, "cache_read_input_tokens": 1e-8,
         "cache_creation_input_tokens": 1.25e-7}
CHARGED = 0.5
SUFFIX = " Show me the evidence and the command you ran."


def key_spend(base):
    keyfile = Path.home() / ".config" / "dfir-lab" / "duke-api-key"
    req = urllib.request.Request(base.rstrip("/") + "/key/info",
                                 headers={"Authorization": "Bearer " + keyfile.read_text().strip()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return float(json.load(r)["info"]["spend"])
    except Exception:  # noqa: BLE001
        return None


def proxy_records(log, start):
    out = []
    try:
        lines = Path(log).read_text().splitlines()
    except OSError:
        return out
    for line in lines[start:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def proxy_len(log):
    try:
        return len(Path(log).read_text().splitlines())
    except OSError:
        return 0


def meter(recs):
    msgs = [r for r in recs if r.get("method") == "POST" and str(r.get("path", "")).startswith("/v1/messages")
            and "count_tokens" not in r.get("path", "")]
    ok = [r for r in msgs if r.get("status") == 200]
    rl = [r for r in msgs if r.get("status") == 429]
    tokens, cost = 0, 0.0
    for r in ok:
        u = r.get("usage") or {}
        for k, p in PRICE.items():
            v = u.get(k) or 0
            tokens += v
            cost += v * p * CHARGED
    # Time lost to rate limits: from the end of each rejected request to the next request.
    wait = 0.0
    for r in rl:
        end = r["t_start"] + (r.get("t_total") or 0)
        nxt = [m["t_start"] for m in msgs if m["t_start"] > r["t_start"]]
        if nxt:
            wait += max(0.0, min(nxt) - end)
    return {"requests": len(msgs), "ok": len(ok), "rate_limited": len(rl),
            "other_errors": len([r for r in msgs if r.get("status") not in (200, 429)]),
            "tokens": tokens, "cost_usd": round(cost, 5), "wait_s": round(wait, 1),
            "max_request_tokens": max([sum((r.get("usage") or {}).get(k) or 0 for k in PRICE)
                                       for r in ok] or [0])}


def parse_stream(path):
    result, session, turns, tools, cost = "", None, None, [], None
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            session = ev.get("session_id")
        elif ev.get("type") == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    tools.append({"tool": b.get("name"),
                                  "input": (inp.get("command") or inp.get("file_path")
                                            or inp.get("pattern") or json.dumps(inp))[:400]})
        elif ev.get("type") == "result":
            result = ev.get("result") or ""
            turns = ev.get("num_turns")
            cost = ev.get("total_cost_usd")
    return {"result": result, "session_id": session, "turns": turns, "tool_calls": tools,
            "claude_cost_estimate": cost}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("cps")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--gap", type=int, default=65)
    ap.add_argument("--stop-spend", type=float, default=0.60)
    ap.add_argument("--log", default="/var/tmp/dfir-dry/proxy.jsonl")
    ap.add_argument("--max-turns", type=int, default=40)
    a = ap.parse_args()
    cfg = Config()
    base = cfg.base_url
    cps = {str(c["id"]): c for c in json.load(open(cfg.case_path("checkpoints")))["checkpoints"]}
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    env = process_env(cfg)
    settings = json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % a.port}})
    spend = key_spend(base)
    for n in a.cps.split(","):
        if spend is not None and spend >= a.stop_spend:
            print("STOP: key spend %.4f >= %.2f" % (spend, a.stop_spend), flush=True)
            break
        cp = cps[n]
        prompt = cp["question"] + SUFFIX
        d = out / ("cp%s" % n)
        d.mkdir(exist_ok=True)
        mark = proxy_len(a.log)
        t0 = time.time()
        with open(d / "stream.jsonl", "w") as so, open(d / "stderr.txt", "w") as se:
            p = subprocess.run([cfg.claude_binary, "-p", prompt, "--output-format", "stream-json",
                                "--verbose", "--max-turns", str(a.max_turns), "--settings", settings],
                               cwd=str(cfg.case_dir), env=env, stdin=subprocess.DEVNULL,
                               stdout=so, stderr=se, timeout=900)
        wall = time.time() - t0
        time.sleep(3)
        m = meter(proxy_records(a.log, mark))
        s = parse_stream(d / "stream.jsonl")
        (d / "result.txt").write_text(s["result"])
        time.sleep(a.gap)
        after = key_spend(base)
        rec = {"cp": int(n), "prompt": prompt, "exit": p.returncode, "wall_s": round(wall, 1),
               "spend_before": spend, "spend_after": after,
               "spend_delta": (round(after - spend, 5) if after is not None and spend is not None
                               else None),
               **m, "turns": s["turns"], "session_id": s["session_id"],
               "tool_calls": s["tool_calls"]}
        (d / "summary.json").write_text(json.dumps(rec, indent=2))
        with open(out / "summary.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print("cp%s exit=%d wall=%.1fs req=%d 429=%d wait=%.1fs tokens=%d cost=$%.4f spend=%s"
              % (n, p.returncode, wall, m["requests"], m["rate_limited"], m["wait_s"],
                 m["tokens"], m["cost_usd"], after), flush=True)
        spend = after
    return 0


if __name__ == "__main__":
    sys.exit(main())
