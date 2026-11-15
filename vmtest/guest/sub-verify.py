#!/usr/bin/env python3
"""Real lab questions on the subscription route, after a person signed in.

For each checkpoint: a fresh non-interactive Claude Code session in the case
folder with the lab's settings and the environment `dfir-lab start` gives
Claude Code on the subscription route, asked the checkpoint's question from
the installed checkpoints.json (the style of dryrun.py), plus a request to end
with "ANSWER: <value>". The answer is checked with `dfir-lab checkpoint`.
Records the model, wall time, turns, tokens, and anything that looks like a
rate limit, a usage limit or a retry.

    sub-verify.py OUTDIR 3,6 [--max-turns 40] [--gap 20]

Writes OUTDIR/cp<N>/{stream.jsonl,stderr.txt,result.txt,summary.json} and
OUTDIR/summary.jsonl. Never opens Claude Code's sign-in file; every text it
writes is passed through the toolkit's redaction (sk-ant-... and the key).
The answer value itself is not printed or kept in the summary.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/usr/local/lib/dfir-lab")
from dfir_lab import configure_claude, redact, route  # noqa: E402
from dfir_lab.claude_env import process_env  # noqa: E402
from dfir_lab.config import Config  # noqa: E402

SUFFIX = (" Show me the evidence and the command you ran. End your reply with one last line "
          "of the form ANSWER: <value>, with nothing else on that line.")
RATE = re.compile(r"\b429\b|rate.?limit|usage limit|you.ve hit your|overloaded|\b529\b|"
                  r"retrying", re.I)


def parse_stream(path):
    out = {"result": "", "turns": None, "models": [], "init_model": None, "retries": 0,
           "rate_events": [], "usage": {}, "cost": None, "is_error": None, "tool_calls": 0}
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t, st = ev.get("type"), ev.get("subtype")
        if t == "system" and st == "init":
            out["init_model"] = ev.get("model")
        elif t == "system" and st and "retry" in str(st):
            out["retries"] += 1
            out["rate_events"].append({k: ev.get(k) for k in ("subtype", "attempt", "error_status",
                                                               "error", "retry_delay_ms")})
        elif t == "assistant":
            msg = ev.get("message") or {}
            m = msg.get("model")
            if m and m not in out["models"]:
                out["models"].append(m)
            out["tool_calls"] += sum(1 for b in msg.get("content") or []
                                     if isinstance(b, dict) and b.get("type") == "tool_use")
        elif t == "result":
            out["result"] = ev.get("result") or ""
            out["turns"] = ev.get("num_turns")
            out["usage"] = ev.get("usage") or {}
            out["cost"] = ev.get("total_cost_usd")
            out["is_error"] = ev.get("is_error")
            for m in (ev.get("modelUsage") or {}):
                if m not in out["models"]:
                    out["models"].append(m)
    return out


def extract_answer(text):
    found = re.findall(r"^\s*\**\s*ANSWER\s*:\s*\**\s*(.+?)\s*$", text or "", re.M | re.I)
    if not found:
        return None
    return found[-1].strip().strip("`*").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("cps")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--gap", type=int, default=20)
    a = ap.parse_args()
    cfg = Config()
    if route.current() != route.SUBSCRIPTION:
        print("STOP: the VM is not on the subscription route (run: dfir-lab use subscription)")
        return 2
    configure_claude.ensure(cfg, quiet=True, route=route.SUBSCRIPTION)
    cps = {str(c["id"]): c for c in json.load(open(cfg.case_path("checkpoints")))["checkpoints"]}
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    env = process_env(cfg, route=route.SUBSCRIPTION)
    env.pop("BROWSER", None)
    ok_all = True
    for i, n in enumerate(a.cps.split(",")):
        if i:
            time.sleep(a.gap)
        cp = cps[n]
        d = out / ("cp%s" % n)
        d.mkdir(exist_ok=True)
        t0 = time.time()
        with open(d / "stream.jsonl", "w") as so, open(d / "stderr.txt", "w") as se:
            p = subprocess.run([cfg.claude_binary, "-p", cp["question"] + SUFFIX,
                                "--output-format", "stream-json", "--verbose",
                                "--max-turns", str(a.max_turns)],
                               cwd=str(cfg.case_dir), env=env, stdin=subprocess.DEVNULL,
                               stdout=so, stderr=se, timeout=1200)
        wall = time.time() - t0
        for f in ("stream.jsonl", "stderr.txt"):
            txt = (d / f).read_text(errors="replace")
            (d / f).write_text(redact.redact(txt))
        s = parse_stream(d / "stream.jsonl")
        (d / "result.txt").write_text(redact.redact(s["result"]))
        stderr = (d / "stderr.txt").read_text(errors="replace")
        answer = extract_answer(s["result"])
        verdict = "no ANSWER line"
        if answer:
            c = subprocess.run(["dfir-lab", "checkpoint", n, answer, "--no-gui"],
                               capture_output=True, text=True, timeout=60)
            verdict = "CORRECT" if "CORRECT" in c.stdout else (
                "not in the expected form" if "expected form" in c.stdout else "not correct")
        rate_hits = sorted(set(m.group(0).lower() for m in RATE.finditer(stderr + "\n"
                                                                          + s["result"])))
        rec = {"cp": int(n), "title": cp.get("title"), "exit": p.returncode,
               "wall_s": round(wall, 1), "init_model": s["init_model"], "models": s["models"],
               "turns": s["turns"], "tool_calls": s["tool_calls"], "is_error": s["is_error"],
               "usage": s["usage"], "claude_cost_estimate_usd": s["cost"],
               "retries": s["retries"], "retry_events": s["rate_events"][:10],
               "rate_limit_words_seen": rate_hits, "checkpoint": verdict}
        (d / "summary.json").write_text(json.dumps(rec, indent=2) + "\n")
        with open(out / "summary.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        ok_all = ok_all and verdict == "CORRECT" and p.returncode == 0
        print("cp%s exit=%d wall=%.1fs model=%s turns=%s retries=%d rate-limit-words=%s "
              "checkpoint=%s" % (n, p.returncode, wall, ",".join(s["models"]) or s["init_model"],
                                 s["turns"], s["retries"], ",".join(rate_hits) or "none",
                                 verdict), flush=True)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
