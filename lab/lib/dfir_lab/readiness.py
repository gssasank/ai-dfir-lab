"""Readiness codes: DFIR-<version>-<R|N>[S]-<10 hex>.

The hex part is the first 10 hex digits of SHA-256 over the lab version,
the UTC date, the route (subscription only) and the ordered check results.
An "S" after R or N marks a check on the optional subscription route; Duke
route codes are unchanged from earlier versions. This is NOT a security
measure: anyone who reads this file can compute a valid code. It only
resists typos and casual editing of a pasted result.
"""

import hashlib
import json
import re

from .errors import LabError, UsageError

ALL_CHECKS = tuple(range(1, 12))
DUKE, SUBSCRIPTION = "duke", "subscription"
# The checks that talk to the Duke gateway with the key; on the subscription
# route they are "not applicable" (NA).
GATEWAY_ONLY = (6, 7, 8, 9)
NA = "NA"
CODE_RE = re.compile(r"^DFIR-(?P<version>.+)-(?P<flag>[RN])(?P<route>S?)-(?P<hex>[0-9a-f]{10})$")


def is_ready(results, route=DUKE):
    """results: list of (id, status). Ready = all 11 checks ran and every
    check that applies to the route passed (PASS or WARN). On the
    subscription route checks 6-9 must be NA; NA anywhere else is not ready."""
    ids = tuple(i for i, _ in results)
    if ids != ALL_CHECKS:
        return False
    if route == SUBSCRIPTION:
        return all((s == NA) if i in GATEWAY_ONLY else (s in ("PASS", "WARN"))
                   for i, s in results)
    return all(s in ("PASS", "WARN") for _, s in results)


def payload(version, results, date, route=DUKE):
    body = ",".join("%d=%s" % (i, s) for i, s in results)
    if route == SUBSCRIPTION:
        return "%s|%s|route=%s|%s" % (version, date, route, body)
    return "%s|%s|%s" % (version, date, body)


def compute(version, results, date, route=DUKE):
    digest = hashlib.sha256(payload(version, results, date, route).encode("utf-8")).hexdigest()
    return "DFIR-%s-%s%s-%s" % (version, "R" if is_ready(results, route) else "N",
                                "S" if route == SUBSCRIPTION else "", digest[:10])


def results_from_json(data):
    try:
        version = str(data["version"])
        date = str(data["date"])
        results = [(int(r["id"]), str(r["status"])) for r in data["results"]]
        route = str(data.get("route") or DUKE)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LabError("The results file does not look like the JSON written by "
                       "'dfir-lab check'.",
                       "Use the readiness-<time>.json file from ~/dfir-lab/readiness/.")
    if route not in (DUKE, SUBSCRIPTION):
        raise LabError("The results file names an unknown route %r." % route,
                       "Use the readiness-<time>.json file from ~/dfir-lab/readiness/.")
    return version, results, date, route


def verify(code, data):
    """Returns (ok, explanation)."""
    code = code.strip()
    m = CODE_RE.match(code)
    if not m:
        raise UsageError("%r is not a readiness code. A code looks like "
                         "DFIR-0.1.0-R-0123456789." % code,
                         "Copy the code exactly as 'dfir-lab check' printed it.")
    version, results, date, route = results_from_json(data)
    expected = compute(version, results, date, route)
    if expected == code:
        return True, "valid: %s, lab version %s, checked on %s, %s route" % (
            "READY" if m.group("flag") == "R" else "NOT READY", version, date,
            "subscription" if route == SUBSCRIPTION else "Duke key")
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
