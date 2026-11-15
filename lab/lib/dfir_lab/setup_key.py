"""dfir-lab setup-key (spec 5.1)."""

import sys

from . import check, fsutil, keystore, route, ui
from .errors import LabError, UsageError
from .messages import CANNOT_REACH

# Outcome sentences (spec 5.1 step 4).
SAVED_WORKING = "Your key is saved and working."
SAVED_UNREACHABLE = "Your key is saved, but the gateway cannot be reached right now, so the key could not be tested."
REJECTED = "Your key is saved, but the gateway rejected it."
MODEL_UNAVAILABLE = "Your key is saved and works, but the course model {model} is not available to it."
RATE_LIMIT = "Your key is saved and valid, but the gateway is holding it back for a minute (too many requests or tokens in the last minute), so the test could not finish."
BUDGET = "Your key is saved and valid, but it has used up its budget until the next reset."
LIMIT = "Your key is saved and valid, but it has reached one of its usage limits (a per-minute limit or its budget)."
BUSY = "Your key is saved, but the AI service behind the gateway is busy or down right now, so the key could not be fully tested."
OTHER = "Your key is saved, but testing it gave an unexpected error."


def read_key(from_stdin):
    if from_stdin:
        return sys.stdin.read()
    ui.out("Paste your personal Duke AI key (from the Duke AI dashboard).")
    ui.out("What you paste stays hidden; press Enter when done.")
    value = ui.ask_secret("Duke AI key:", title="Set up Duke AI access")
    if value is None:
        raise LabError("No key was entered, so nothing was saved.",
                       "Run dfir-lab setup-key again when you have your key.")
    return value


def show_status():
    st = keystore.status()
    if not st["present"]:
        ui.out("No Duke AI key is stored.")
        ui.out("To store one, run: dfir-lab setup-key")
        return 1
    ui.out("A Duke AI key is stored: %d characters, ending in ...%s." % (st["length"], st["last4"]))
    ui.out("File: %s (permissions %o%s)" % (st["path"], st["mode"],
                                            "" if st["mode"] == 0o600 else
                                            " - should be 600; run dfir-lab setup-key again"))
    return 0


def test_key(cfg):
    """Run checks 5-7 and return (exit_code, headline, detail, todo)."""
    # The key is always tested against the Duke gateway, whichever route is active.
    ctx = check.run_checks(cfg, [4, 5, 6, 7], route=route.DUKE)
    r5, r6, r7 = ctx.results[5], ctx.results[6], ctx.results[7]
    model = cfg.model_main
    if r5.status == check.FAIL:
        return 1, SAVED_UNREACHABLE, r5.result, r5.todo
    for r in (r6, r7):
        kind = r.data.get("outcome", "")
        if r.status == check.FAIL and kind in CANNOT_REACH:
            return 1, SAVED_UNREACHABLE, r.result, r.todo
        if r.status == check.FAIL and kind == "key_rejected":
            return 1, REJECTED, r.result, r.todo
        if kind in ("model_forbidden", "model_unknown", "model_missing"):
            if r.status == check.WARN and r7.status == check.PASS:
                continue  # listed oddly, but the model answers: good enough
            return 1, MODEL_UNAVAILABLE.format(model=model), r.result, r.todo
        if r.status == check.FAIL and kind == "rate_limit":
            return 1, RATE_LIMIT, r.result, r.todo
        if r.status == check.FAIL and kind == "budget":
            return 1, BUDGET, r.result, r.todo
        if r.status == check.FAIL and kind == "limit":
            return 1, LIMIT, r.result, r.todo
        if r.status == check.FAIL and kind == "busy":
            return 1, BUSY, r.result, r.todo
        if r.status == check.FAIL:
            return 1, OTHER, r.result, r.todo
        if r.status == check.SKIP:
            continue
    if r7.status == check.PASS:
        detail = r7.result
        limits = (r6.data or {}).get("limits")
        if limits:
            summary = r6.result.split("; key limits: ", 1)[-1]
            detail += "\nYour key's limits: %s." % summary
        return 0, SAVED_WORKING, detail, ""
    return 1, OTHER, r7.result, r7.todo


def run(cfg, from_stdin=False, clear=False, status=False):
    if sum(map(bool, (from_stdin and (clear or status), clear and status))):
        raise UsageError("Use only one of --clear, --status, --from-stdin.",
                         "Run: dfir-lab setup-key --help")
    if status:
        return show_status()
    if clear:
        removed = keystore.clear()
        fsutil.log("setup-key.clear", removed=removed)
        ui.out("The stored Duke AI key was deleted." if removed else "No key was stored.")
        return 0
    cfg.require_all({("gateway", "base_url"), ("gateway", "model_main")})
    raw = read_key(from_stdin)
    key = keystore.normalize(raw)
    found = keystore.problems(key)
    if found:
        fsutil.log("setup-key.rejected", reasons=len(found))
        raise LabError("The key was not saved. " + " ".join(found),
                       "Copy the whole key again from the Duke AI dashboard and run "
                       "dfir-lab setup-key again.")
    path = keystore.save(key)
    ui.out("Saved the key to %s (only your account can read it)." % path)
    ui.out("Testing it against the gateway...")
    code, headline, detail, todo = test_key(cfg)
    fsutil.log("setup-key.saved", tested=headline)
    ui.out("")
    ui.out(headline)
    if code != 0:
        ui.out(detail)
        if todo:
            ui.out("What to do: " + todo)
    else:
        ui.out(detail)
        if route.is_subscription():
            ui.out("Note: the lab uses your own Claude subscription right now. To use this key "
                   "instead, run: dfir-lab use duke")
        else:
            ui.out("Next: double-click 'Start DFIR Lab' or run: dfir-lab start")
    ui.notify("Set up Duke AI access", headline + "\n\n" + (todo or detail), error=code != 0)
    return code
