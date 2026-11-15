"""dfir-lab use subscription | duke | --status: choose how Claude Code
reaches a model.

Switching rewrites the course entries in ~/.claude/settings.json for the
chosen route in one atomic write (configure_claude.apply), then stores the
route in ~/dfir-lab/route.json. Nothing is deleted: the stored Duke key and a
Claude sign-in both stay on the VM. Running it again changes nothing.
"""

from . import configure_claude, fsutil, keystore, paths, route, subscription, ui
from .claude_env import find_claude, require_min_version
from .errors import LabError, UsageError
from .messages import render

LOGOUT_HINT = ("To sign this VM out of your Claude account, run: claude auth logout")


def switch(cfg, target):
    """Apply the settings for `target`, then store it. Returns True when the
    settings file changed. If the route cannot be stored, the previous route's
    settings are put back, so the settings and the stored route never
    disagree."""
    previous = route.current()
    changed, _, _ = configure_claude.apply(cfg, route=target)
    try:
        route.save(target)
    except OSError as exc:
        try:
            configure_claude.apply(cfg, route=previous)
        except LabError:
            pass
        raise LabError("Could not save your choice in %s (%s); nothing was changed."
                       % (paths.route_file(), exc.strerror or exc),
                       "Check that your home folder is not full, then try again.")
    ok, why = configure_claude.prepare_first_run(cfg, route=target)
    if not ok:
        fsutil.log("use.first-run-not-seeded", reason=why)
    fsutil.log("use", route=target, previous=previous, settings_changed=changed)
    return changed


def _key_line():
    st = keystore.status()
    if st["present"]:
        return "A Duke AI key is stored (%d characters, ending in ...%s)." % (st["length"],
                                                                             st["last4"])
    return None


def use_duke(cfg):
    cfg.require_all({("gateway", "base_url"), ("gateway", "model_main")})
    was = route.current()
    switch(cfg, route.DUKE)
    if was == route.DUKE:
        ui.out("The lab already uses your Duke AI Gateway key (settings checked).")
    else:
        ui.out("Done: the lab now uses your Duke AI Gateway key again, with the course model "
               "%s." % cfg.model_main)
    key = _key_line()
    if key:
        ui.out(key)
        ui.out("Next: off campus, connect the Duke VPN on your own computer. Then double-click "
               "\"Start DFIR Lab\" or run: dfir-lab start")
    else:
        ui.out("No Duke AI key is stored yet. Double-click \"Set up Duke AI access\" on the "
               "desktop, or run: dfir-lab setup-key")
    ui.out("If you signed in with your Claude account, that sign-in stays on this VM unused. "
           + LOGOUT_HINT)
    return 0


def use_subscription(cfg):
    cfg.require_all()
    exe = find_claude(cfg)
    require_min_version(cfg, exe)
    ui.out("Switching the lab's AI assistant to your own Claude subscription (Pro, Max or "
           "higher).")
    ui.out("It uses your subscription's own allowance and needs no Duke key and no VPN. Your "
           "Duke key stays stored; switch back any time with: dfir-lab use duke")
    ui.out("")
    switch(cfg, route.SUBSCRIPTION)
    st = subscription.auth_status(cfg, exe)
    if st.signed_in:
        ui.out("Claude Code is already %s." % subscription.describe(st))
    else:
        if st.logged_in and not st.expired:
            ui.out("Claude Code is %s; signing in with your subscription now." %
                   subscription.describe(st))
        elif st.expired:
            ui.out("Your Claude sign-in has expired; signing in again.")
        subscription.sign_in(cfg, exe)
        st = subscription.auth_status(cfg, exe)
    if not st.signed_in:
        if st.expired:
            what, todo = render("sub_login_expired", cfg, detail="the sign-in has expired")
        elif st.logged_in:
            what, todo = render("sub_wrong_login", cfg, detail=subscription.describe(st))
        elif not st.ok:
            what, todo = ("Could not confirm the sign-in: %s." % st.detail,
                          "Run  dfir-lab use subscription  again. If it fails again, run  "
                          "dfir-lab report-problem  and send the file to the TA.")
        else:
            what = "Claude Code is not signed in with your Claude subscription."
            todo = ("Run  dfir-lab use subscription  again and finish the sign-in in Chromium. "
                    "To use your Duke key instead, run  dfir-lab use duke .")
        fsutil.log("use.subscription.not-signed-in", method=st.method)
        raise LabError(what, todo)
    account = (" (account: %s)" % st.email) if st.email else ""
    ui.out("Done: Claude Code is %s%s." % (subscription.describe(st), account))
    ui.out("It will use %s. You can pick another model inside Claude Code with /model."
           % subscription.model_text(cfg))
    ui.out("Next: double-click \"Start DFIR Lab\" or run: dfir-lab start")
    fsutil.log("use.subscription.signed-in", method=st.method, plan=st.plan)
    return 0


def show_status(cfg):
    current = route.current()
    ui.out("AI access: %s." % route.LABELS[current])
    if current == route.SUBSCRIPTION:
        try:
            exe = find_claude(cfg)
            st = subscription.auth_status(cfg, exe)
            ui.out("Claude sign-in: %s." % subscription.describe(st))
        except LabError as exc:
            ui.out("Claude sign-in: unknown (%s)" % exc.what)
        ui.out("Model: %s." % subscription.model_text(cfg))
        key = _key_line()
        ui.out(("Duke key (not used now): " + key) if key else
               "Duke key: none stored.")
        ui.out("To use your Duke key instead: dfir-lab use duke")
    else:
        key = _key_line()
        ui.out(key or "No Duke AI key is stored yet (run: dfir-lab setup-key).")
        ui.out("To use your own Claude subscription instead: dfir-lab use subscription")
    ui.out(LOGOUT_HINT)
    return 0


def run(cfg, target=None, status=False):
    if status and target:
        raise UsageError("Use either a route or --status, not both.",
                         "Run: dfir-lab use subscription | dfir-lab use duke | "
                         "dfir-lab use --status")
    if status or not target:
        return show_status(cfg)
    if target == route.SUBSCRIPTION:
        return use_subscription(cfg)
    if target == route.DUKE:
        return use_duke(cfg)
    raise UsageError("There is no route %r." % target,
                     "Run: dfir-lab use subscription  or  dfir-lab use duke")
