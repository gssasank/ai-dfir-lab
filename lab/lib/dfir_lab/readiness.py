"""Readiness codes: DFIR-<version>-<R|N>-<10 hex>.

The hex part is the first 10 hex digits of SHA-256 over the lab version,
the ordered check results, and the UTC date. This is NOT a security
measure: anyone who reads this file can compute a valid code. It only
resists typos and casual editing of a pasted result.
"""

import hashlib
import json
import re

from .errors import LabError, UsageError

ALL_CHECKS = tuple(range(1, 12))
CODE_RE = re.compile(r"^DFIR-(?P<version>.+)-(?P<flag>[RN])-(?P<hex>[0-9a-f]{10})$")


def is_ready(results):
    """results: list of (id, status). Ready = all 11 checks ran, none FAILED."""
    ids = tuple(i for i, _ in results)
    if ids != ALL_CHECKS:
        return False
    return all(s in ("PASS", "WARN") for _, s in results)


def payload(version, results, date):
    body = ",".join("%d=%s" % (i, s) for i, s in results)
    return "%s|%s|%s" % (version, date, body)


def compute(version, results, date):
    digest = hashlib.sha256(payload(version, results, date).encode("utf-8")).hexdigest()
    return "DFIR-%s-%s-%s" % (version, "R" if is_ready(results) else "N", digest[:10])


def results_from_json(data):
    try:
        version = str(data["version"])
        date = str(data["date"])
        results = [(int(r["id"]), str(r["status"])) for r in data["results"]]
    except (KeyError, TypeError, ValueError):
        raise LabError("The results file does not look like the JSON written by "
                       "'dfir-lab check'.",
                       "Use the readiness-<time>.json file from ~/dfir-lab/readiness/.")
    return version, results, date


def verify(code, data):
    """Returns (ok, explanation)."""
    code = code.strip()
    m = CODE_RE.match(code)
    if not m:
        raise UsageError("%r is not a readiness code. A code looks like "
                         "DFIR-0.1.0-R-0123456789." % code,
                         "Copy the code exactly as 'dfir-lab check' printed it.")
    version, results, date = results_from_json(data)
    expected = compute(version, results, date)
    if expected == code:
        return True, "valid: %s, lab version %s, checked on %s" % (
            "READY" if m.group("flag") == "R" else "NOT READY", version, date)
    return False, "does not match these results (expected %s)" % expected


def load_results_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        raise UsageError("Cannot read %s (%s)." % (path, exc.strerror or exc),
                         "Give the path of a readiness-<time>.json file.")
    except ValueError:
        raise UsageError("%s is not valid JSON." % path,
                         "Give the path of a readiness-<time>.json file.")
