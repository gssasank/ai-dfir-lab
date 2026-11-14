"""dfir-lab replay (spec 5.5): step through a recorded session.

Recording format: JSON Lines, one record per line:
  {"type": "prompt", "text": "..."}
  {"type": "assistant_text", "text": "..."}
  {"type": "tool_call", "tool": "Bash", "command": "...", "args": {...}}
  {"type": "tool_result", "output": "...", "truncated": false, "note": ""}
  {"type": "pause", "question": "..."}
"""

import json
import shutil
import textwrap

from . import fsutil, ui
from .errors import LabError, UsageError
from .redact import redact

TYPES = ("prompt", "assistant_text", "tool_call", "tool_result", "pause")
LABELS = {
    "prompt": "PROMPT (what the analyst typed)",
    "assistant_text": "AI",
    "tool_call": "AI RUNS A TOOL",
    "tool_result": "TOOL OUTPUT",
    "pause": "YOUR TURN - think before you continue",
}
HELP = "[Enter] next   b back   j N jump to step N   q quit   ? help"


def load_recording(path):
    records = []
    with open(path, encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                raise LabError("Line %d of the recording %s is damaged." % (n, path),
                               "Tell the TA; the case kit needs fixing.")
            if not isinstance(rec, dict) or rec.get("type") not in TYPES:
                raise LabError("Line %d of the recording %s has an unknown record type." % (n, path),
                               "Tell the TA; the case kit needs fixing.")
            records.append(rec)
    if not records:
        raise LabError("The recording %s is empty." % path, "Tell the TA.")
    return records


def render_record(rec, width=78):
    t = rec["type"]
    if t in ("prompt", "assistant_text"):
        body = rec.get("text", "")
    elif t == "tool_call":
        tool = rec.get("tool") or "Bash"
        cmd = rec.get("command")
        if cmd is None:
            cmd = json.dumps(rec.get("args") or {}, indent=2)
        body = "%s: %s" % (tool, cmd)
    elif t == "tool_result":
        body = rec.get("output", "")
        if rec.get("truncated"):
            body += "\n[%s]" % (rec.get("note") or "output truncated in this recording")
        elif rec.get("note"):
            body += "\n[%s]" % rec["note"]
    else:
        body = rec.get("question", "")
    # Redact the whole record before it is split, wrapped or paged: a key
    # broken across two output lines must not survive (review H2).
    body = redact(str(body))
    lines = []
    for para in body.splitlines() or [""]:
        if t == "tool_result" or len(para) <= width:
            lines.append(para)
        else:
            lines.extend(textwrap.wrap(para, width) or [""])
    return lines


class Player:
    def __init__(self, records, input_fn=input, output_fn=None, page_height=None):
        self.records = records
        self.input = input_fn
        self.output = output_fn or ui.out
        self.page_height = page_height or max(10, shutil.get_terminal_size((80, 24)).lines - 4)
        self.pos = 0

    def _ask(self, prompt):
        self.output(prompt, end="") if self.output is ui.out else self.output(prompt)
        try:
            return self.input().strip()
        except (EOFError, KeyboardInterrupt):
            return "q"

    def show(self, i):
        rec = self.records[i]
        self.output("")
        self.output("--- Step %d of %d: %s ---" % (i + 1, len(self.records), LABELS[rec["type"]]))
        lines = render_record(rec)
        h = self.page_height
        k = 0
        while k < len(lines):
            for line in lines[k:k + h]:
                self.output(line)
            k += h
            if k < len(lines):
                ans = self._ask("-- more (%d of %d lines) - Enter for more, s to skip the rest -- "
                                % (k, len(lines)))
                if ans.lower() in ("s", "q"):
                    self.output("[rest of the output skipped]")
                    break

    def run(self):
        self.output("Offline replay: %d steps. %s" % (len(self.records), HELP))
        self.show(self.pos)
        while True:
            cmd = self._ask("\n%s > " % ("[end] b back, j N jump, q quit" if
                                         self.pos == len(self.records) - 1 else HELP))
            low = cmd.lower()
            if low in ("q", "quit", "exit"):
                self.output("Replay closed at step %d of %d." % (self.pos + 1, len(self.records)))
                return self.pos
            if low in ("?", "h", "help"):
                self.output(HELP)
                continue
            if low in ("b", "back", "p"):
                if self.pos == 0:
                    self.output("This is the first step.")
                    continue
                self.pos -= 1
                self.show(self.pos)
                continue
            if low.startswith("j") or low.isdigit():
                num = low[1:].strip() if low.startswith("j") else low
                if not num.isdigit() or not 1 <= int(num) <= len(self.records):
                    self.output("Type j and a step number from 1 to %d, e.g. j 5." % len(self.records))
                    continue
                self.pos = int(num) - 1
                self.show(self.pos)
                continue
            if low in ("", "n", "next"):
                if self.pos >= len(self.records) - 1:
                    self.output("End of the recording. Type q to quit or b to go back.")
                    continue
                self.pos += 1
                self.show(self.pos)
                continue
            self.output("Unknown command %r. %s" % (cmd, HELP))


def recordings(cfg):
    d = cfg.case_path("replay_dir")
    if not d or not d.is_dir():
        return d, []
    return d, sorted(p for p in d.iterdir() if p.suffix == ".jsonl" and p.is_file())


def run(cfg, name=None, list_only=False, input_fn=input):
    d, recs = recordings(cfg)
    if not recs:
        raise LabError("No offline recordings were found in %s." % d,
                       "The case kit is incomplete. Tell the TA.", code=3)
    if list_only:
        ui.out("Recordings in %s:" % d)
        for p in recs:
            ui.out("  " + p.stem)
        return 0
    if name:
        match = [p for p in recs if p.stem == name or p.name == name]
        if not match:
            raise UsageError("There is no recording called %r." % name,
                             "Available: %s" % ", ".join(p.stem for p in recs))
        path = match[0]
    elif len(recs) == 1:
        path = recs[0]
    else:
        choice = ui.ask_choice("Which recording would you like to replay?",
                               [(str(p), p.stem) for p in recs], title="Offline replay")
        if not choice:
            return 1
        path = next(p for p in recs if str(p) == choice)
    records = load_recording(path)
    fsutil.log("replay", recording=path.stem)
    Player(records, input_fn=input_fn).run()
    return 0
