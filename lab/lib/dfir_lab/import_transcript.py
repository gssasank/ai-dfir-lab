"""dfir-lab-import-transcript (instructor tool): convert a Claude Code
transcript (~/.claude/projects/<project>/<session>.jsonl) into the replay
format, applying redaction.

The transcript layout is not formally documented by Anthropic. This reader
handles what Claude Code writes today: one JSON object per line with
"type": "user" | "assistant" and a "message" in Anthropic Messages format.
Other line types (summaries, system notes) are skipped.
"""

import argparse
import json
import re
import sys

from . import redact as redact_mod

MAX_RESULT_LINES = 200
# Strip text Claude Code injects into user turns.
_INJECTED = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+)>.*?</\1>", re.S)


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


def _truncate(text, max_lines):
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False, ""
    note = "output truncated: showing %d of %d lines" % (max_lines, len(lines))
    return "\n".join(lines[:max_lines]), True, note


def convert(lines, redactor=None, max_lines=MAX_RESULT_LINES):
    red = redactor or redact_mod.get()
    out = []

    def add(rec):
        for k, v in list(rec.items()):
            if isinstance(v, str):
                rec[k] = red.redact(v)
            elif isinstance(v, (dict, list)):
                rec[k] = json.loads(red.redact(json.dumps(v)))
        out.append(rec)

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict) or obj.get("isMeta") or obj.get("isSidechain"):
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        kind = obj.get("type") or msg.get("role")
        content = msg.get("content")
        if kind == "user":
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                 for b in content):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        # Redact the whole output before cutting it to max_lines.
                        text, trunc, note = _truncate(red.redact(_text_of(b.get("content"))),
                                                      max_lines)
                        add({"type": "tool_result", "output": text, "truncated": trunc,
                             "note": note})
                continue
            text = _INJECTED.sub("", _text_of(content)).strip()
            if text:
                add({"type": "prompt", "text": text})
        elif kind == "assistant":
            blocks = content if isinstance(content, list) else [{"type": "text", "text": _text_of(content)}]
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    add({"type": "assistant_text", "text": b["text"].strip()})
                elif b.get("type") == "tool_use":
                    inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                    cmd = inp.get("command")
                    if cmd is None:
                        cmd = inp.get("file_path") or inp.get("pattern") or json.dumps(inp)
                    add({"type": "tool_call", "tool": b.get("name", ""), "command": cmd,
                         "args": inp})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dfir-lab-import-transcript",
                                 description="Convert a Claude Code transcript to a replay recording.")
    ap.add_argument("transcript", help="Claude Code transcript (.jsonl)")
    ap.add_argument("-o", "--output", default="-", help="output .jsonl (default: stdout)")
    ap.add_argument("--max-lines", type=int, default=MAX_RESULT_LINES,
                    help="truncate tool output longer than this many lines")
    ap.add_argument("--redact", action="append", default=[], metavar="FILE",
                    help="also redact the secret stored in FILE (repeatable)")
    args = ap.parse_args(argv)
    red = redact_mod.get()
    for f in args.redact:
        try:
            with open(f, encoding="utf-8") as fh:
                red.add(fh.read().strip())
        except OSError as exc:
            sys.stderr.write("cannot read %s: %s\n" % (f, exc))
            return 1
    try:
        with open(args.transcript, encoding="utf-8", errors="replace") as fh:
            records = convert(fh, red, args.max_lines)
    except OSError as exc:
        sys.stderr.write("cannot read %s: %s\n" % (args.transcript, exc))
        return 1
    text = "".join(json.dumps(r) + "\n" for r in records)
    text = red.redact(text)
    if args.output == "-":
        sys.stdout.write(text)
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stderr.write("wrote %d records to %s\n" % (len(records), args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
