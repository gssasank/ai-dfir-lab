"""dfir-lab start (spec 5.2)."""

import os

from . import (casekit, configure_claude, disk, fsutil, keystore, paths, route as route_mod,
               setup_key, subscription, ui)
from .claude_env import find_claude, process_env, require_min_version
from .errors import ConfigError, LabError
from .gateway import probe_network
from .messages import render

QUICK_TIMEOUT_S = 5.0


def _reachable(cfg):
    timeout = min(QUICK_TIMEOUT_S, cfg.connect_timeout)
    return probe_network(cfg.base_url, timeout)


def _anthropic_reachable(cfg):
    """The subscription route talks to Anthropic's API directly (no VPN)."""
    timeout = min(QUICK_TIMEOUT_S, cfg.connect_timeout)
    return probe_network(cfg.subscription_hosts[0], timeout)


def banner(cfg, trust_note="", disk_line="", route=None):
    route = route_mod.resolve(route)
    sub = route == route_mod.SUBSCRIPTION
    ws = cfg.case_path("worksheet")
    ticket = cfg.case_path("ticket")
    report = cfg.case_path("ai_report")
    lines = [
        "=" * 64,
        " %s - DFIR Lab" % cfg.course,
        " Case: %s" % cfg.case_title,
        " Case folder: %s" % cfg.case_dir,
    ]
    if sub:
        lines.append(" AI access: your own Claude subscription (%s)"
                     % subscription.model_text(cfg))
    else:
        lines.append(" AI access: your Duke AI Gateway key")
    if ticket and ticket.exists():
        lines.append(" Incident ticket: %s" % ticket)
    if report and report.exists():
        lines.append(" AI report to check (worksheet Section C): %s" % report)
    if disk_line:
        lines.append(disk_line)
    if ws:
        lines.append(" Worksheet: %s" % ws)
    lines.append(" Type /clear in Claude Code before you start each new checkpoint:")
    if sub:
        lines.append("   a fresh conversation keeps answers focused and uses less of your "
                     "subscription.")
    else:
        lines.append("   short conversations are fast; long ones get slow.")
    lines += [
        " Save your work in analysis/ and reports/ inside the case folder.",
        " Check a checkpoint answer (in another terminal):  dfir-lab checkpoint 1 <answer>",
        " Get a hint:  dfir-lab hint 1      See progress:  dfir-lab progress",
        " Something wrong?  dfir-lab check   then   dfir-lab report-problem",
        " Type /exit in Claude Code to leave.",
    ]
    if trust_note:
        lines.append(trust_note)
    lines.append("=" * 64)
    return "\n".join(lines)


def mount_disk(cfg):
    """Mount the case's disk image if it is not mounted. Returns the banner
    line. If mounting fails the student may still start Claude Code: the
    log-based checkpoints do not need the image."""
    ds = disk.settings(cfg)
    if ds is None:
        return ""
    try:
        disk.mount(ds)
        return " Disk image: mounted read-only at %s" % ds.mount_point
    except LabError as exc:
        ui.problem(exc.what, exc.todo)
        fsutil.log("start.mount-failed", what=exc.what)
        try:
            go = ui.ask_yes_no("Start Claude Code anyway? Checkpoints that use the disk image "
                               "will not work until it is mounted.", default=True,
                               title="Start DFIR Lab")
        except LabError:      # no terminal and no window to ask in
            go = True
        if not go:
            raise LabError("Start was cancelled.", "Run  dfir-lab mount , then  dfir-lab start .")
        return " Disk image: NOT MOUNTED (run  dfir-lab mount  in another terminal)"


def _offer_replay_or_wait(cfg, question, retry_label, replay_fn):
    """Shared by both routes when the service cannot be reached. Returns
    "retry", "anyway", or the replay's exit code."""
    choice = ui.ask_choice(
        question,
        [("retry", retry_label),
         ("anyway", "Start Claude Code anyway"),
         ("replay", "Open the offline replay instead")],
        title="Start DFIR Lab")
    if choice in ("retry", "anyway"):
        return choice
    if choice == "replay":
        if replay_fn is None:
            from . import replay
            replay_fn = replay.run
        return replay_fn(cfg, None)
    raise LabError("Start was cancelled.", "Run dfir-lab start again when ready.")


def _duke_ready(cfg, replay_fn):
    """Duke route: a key is stored and the gateway answers. Returns None to go
    on, or an exit code."""
    # 1. A key must be stored.
    if not keystore.load():
        ui.out("No Duke AI key is stored yet.")
        if not ui.ask_yes_no("Set up your Duke AI key now?", default=True):
            raise LabError("Claude Code needs your Duke AI key, so it was not started.",
                           "Run dfir-lab setup-key when you have your key.")
        code = setup_key.run(cfg)
        if not keystore.load():
            return code or 1

    # 2. Quick reachability test.
    while True:
        net = _reachable(cfg)
        if net.ok:
            return None
        what, todo = render(net.kind, cfg, host=net.host, port=net.port, detail=net.detail)
        ui.problem(what, todo)
        choice = _offer_replay_or_wait(
            cfg, "The gateway cannot be reached. What would you like to do?",
            "Try again (after connecting the VPN)", replay_fn)
        if choice == "retry":
            continue
        if choice == "anyway":
            return None
        return choice


def _subscription_ready(cfg, exe, replay_fn):
    """Subscription route: Claude Code is signed in with a subscription and
    Anthropic's API answers. Never mentions the Duke gateway or the VPN.
    Returns None to go on, or an exit code."""
    # The settings must already be the subscription's before asking Claude
    # Code about its sign-in (an old gateway helper would answer instead).
    try:
        configure_claude.ensure(cfg, route=route_mod.SUBSCRIPTION)
    except LabError as exc:
        raise ConfigError(exc.what + " Claude Code was not started, because it would run "
                          "without the course settings.", exc.todo)
    st = subscription.auth_status(cfg, exe)
    if not st.signed_in:
        kind = ("sub_login_expired" if st.expired else
                "sub_wrong_login" if st.logged_in else "sub_not_signed_in")
        what, _ = render(kind, cfg, detail=subscription.describe(st))
        ui.out(what)
        if not ui.interactive():
            raise LabError("Claude Code was not started, because it is not signed in with your "
                           "Claude subscription.",
                           "Double-click \"Use my Claude subscription\" on the desktop (or run  "
                           "dfir-lab use subscription  in a terminal) and sign in. To use your "
                           "Duke key instead, run  dfir-lab use duke .")
        if not ui.ask_yes_no("Sign in to your Claude account now?", default=True,
                             title="Start DFIR Lab"):
            raise LabError("Claude Code was not started, because it is not signed in with your "
                           "Claude subscription.",
                           "Run  dfir-lab use subscription  to sign in, or  dfir-lab use duke  "
                           "to use your Duke key instead.")
        subscription.sign_in(cfg, exe)
        st = subscription.auth_status(cfg, exe)
        if not st.signed_in:
            raise LabError("Claude Code is still not signed in with your Claude subscription, "
                           "so it was not started.",
                           "Run  dfir-lab use subscription  and finish the sign-in in Chromium, "
                           "or run  dfir-lab use duke  to use your Duke key instead.")
        ui.out("Signed in: Claude Code is %s." % subscription.describe(st))
    while True:
        net = _anthropic_reachable(cfg)
        if net.ok:
            return None
        what, todo = render("sub_no_network", cfg, **subscription.net_fields(net))
        ui.problem(what, todo)
        choice = _offer_replay_or_wait(
            cfg, "Anthropic's service cannot be reached. What would you like to do?",
            "Try again", replay_fn)
        if choice == "retry":
            continue
        if choice == "anyway":
            return None
        return choice


def run(cfg, passthrough=None, replay_fn=None):
    cfg.require_all()
    route = route_mod.current()
    exe = find_claude(cfg)
    require_min_version(cfg, exe)

    # 1-2. Credential and network, per route.
    if route == route_mod.SUBSCRIPTION:
        code = _subscription_ready(cfg, exe, replay_fn)
    else:
        code = _duke_ready(cfg, replay_fn)
    if code is not None:
        return code

    # 3. Output folders (never inside evidence) and the worksheet.
    casekit.ensure_output_dirs(cfg)
    casekit.ensure_worksheet(cfg)

    # The course settings must be in place, or Claude Code is not started
    # (spec 6.4).
    try:
        configure_claude.ensure(cfg, route=route)
    except LabError as exc:
        raise ConfigError(exc.what + " Claude Code was not started, because it would run "
                          "without the course settings.", exc.todo)
    # First interactive start: pre-accept the trust question for the case
    # folder (and, on the subscription route, the first-run screens). If that
    # fails, the student sees the question once.
    trusted, why = configure_claude.prepare_first_run(cfg, route=route)
    if not trusted:
        fsutil.log("start.trust-not-seeded", reason=why)
    note = "" if trusted else (
        " If Claude Code asks whether you trust this folder, choose "
        "\"Yes, I trust this folder\" and press Enter.")

    # The disk image: mounted read-only before Claude Code starts (nothing is
    # mounted after a restart of the VM).
    disk_line = mount_disk(cfg)

    # 4. Banner.
    ui.out(banner(cfg, note, disk_line, route))

    # 6. Session log (before exec, which does not return).
    fsutil.append_line(paths.sessions_log(), "%s lab=%s case=%s route=%s" % (
        fsutil.utc_iso(), cfg.lab_version, cfg.case_id, route))
    fsutil.log("start", case=cfg.case_id, route=route)

    # 5. Replace this process with Claude Code in the case folder.
    os.chdir(str(cfg.case_dir))
    argv = [exe] + list(passthrough or [])
    env = process_env(cfg, route=route)
    if os.environ.get("DFIR_LAB_NO_EXEC") == "1":   # used by the tests
        ui.out("[no-exec] would run: %s in %s" % (" ".join(argv), cfg.case_dir))
        return 0
    os.execve(exe, argv, env)
    return 0  # not reached
