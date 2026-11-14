"""dfir-lab-make-checkpoints (instructor tool, not installed in the student
image): build checkpoints.json from a clear-text JSON source so hashes are
never typed by hand.

Source format:
{
  "case": "sample-case",
  "checkpoints": [
    {"id": 1, "title": "...", "question": "...", "normalize": "integer",
     "answer": "4625", "alternates": ["..."],
     "pattern": null, "then": null,
     "hints": ["...", "..."], "success": "..."}
  ]
}
Each answer and alternate is normalised with the checkpoint's rule before
hashing, and the build fails if an answer does not normalise.
"""

import argparse
import json
import secrets
import sys

from .checkpoint import answer_hash
from .normalize import RULES, FormatProblem, normalize

FIELDS_OUT = ("id", "title", "question", "normalize", "pattern", "then")


def build(source, salt_fn=None):
    salt_fn = salt_fn or (lambda: secrets.token_hex(16))
    if not isinstance(source, dict) or not isinstance(source.get("checkpoints"), list):
        raise ValueError("source must be an object with a 'checkpoints' list")
    out = {"format": 1, "case": source.get("case", ""), "checkpoints": []}
    seen = set()
    for cp in source["checkpoints"]:
        cid = cp.get("id")
        if cid is None or str(cid) in seen:
            raise ValueError("every checkpoint needs a unique id (problem at %r)" % cid)
        seen.add(str(cid))
        rule = cp.get("normalize", "exact")
        if rule not in RULES:
            raise ValueError("checkpoint %s: unknown normalize rule %r" % (cid, rule))
        if rule == "regex-extract" and not cp.get("pattern"):
            raise ValueError("checkpoint %s: regex-extract needs a pattern" % cid)
        if "answer" not in cp:
            raise ValueError("checkpoint %s: missing 'answer'" % cid)
        salt = salt_fn()
        def h(ans):
            try:
                return answer_hash(salt, normalize(rule, str(ans), cp.get("pattern"),
                                                   cp.get("then")))
            except FormatProblem as exc:
                raise ValueError("checkpoint %s: answer %r does not fit rule %s (%s)"
                                 % (cid, ans, rule, exc))
        entry = {k: cp.get(k) for k in FIELDS_OUT}
        entry.update(
            salt=salt,
            hash=h(cp["answer"]),
            alternates=[h(a) for a in cp.get("alternates") or []],
            hints=list(cp.get("hints") or []),
            success=cp.get("success", "Correct."),
        )
        out["checkpoints"].append(entry)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dfir-lab-make-checkpoints",
                                 description="Build checkpoints.json from a clear-text source.")
    ap.add_argument("source", help="clear-text checkpoints source (JSON)")
    ap.add_argument("-o", "--output", default="-", help="output file (default: stdout)")
    args = ap.parse_args(argv)
    try:
        with open(args.source, encoding="utf-8") as fh:
            src = json.load(fh)
        result = build(src)
    except (OSError, ValueError) as exc:
        sys.stderr.write("dfir-lab-make-checkpoints: %s\n" % exc)
        return 1
    text = json.dumps(result, indent=2) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        sys.stderr.write("wrote %s (%d checkpoints)\n" % (args.output, len(result["checkpoints"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
