"""dfir-lab start (spec 5.2)."""

import os

from . import casekit, configure_claude, fsutil, keystore, paths, setup_key, ui
from .claude_env import find_claude, process_env, require_min_version
from .errors import ConfigError, LabError
from .gateway import probe_network
from .messages import render

QUICK_TIMEOUT_S = 5.0


def _reachable(cfg):
    timeout = min(QUICK_TIMEOUT_S, cfg.connect_timeout)
    return probe_network(cfg.base_url, timeout)


def banner(cfg, trust_note=""):
    ws = cfg.case_path("worksheet")
    ticket = cfg.case_path("ticket")
    lines = [
        "=" * 64,
        " %s - DFIR Lab" % cfg.course,
        " Case: %s" % cfg.case_title,
        " Case folder: %s" % cfg.case_dir,
    ]
    if ticket and ticket.exists():
        lines.append(" Incident ticket: %s" % ticket)
    if ws:
        lines.append(" Worksheet: %s" % ws)
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


def run(cfg, passthrough=None, replay_fn=None):
    cfg.require_all()
    exe = find_claude(cfg)
    require_min_version(cfg, exe)

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
            break
        what, todo = render(net.kind, cfg, host=net.host, port=net.port, detail=net.detail)
        ui.problem(what, todo)
        choice = ui.ask_choice(
            "The gateway cannot be reached. What would you like to do?",
            [("retry", "Try again (after connecting the VPN)"),
             ("anyway", "Start Claude Code anyway"),
             ("replay", "Open the offline replay instead")],
            title="Start DFIR Lab")
        if choice == "retry":
            continue
        if choice == "anyway":
            break
        if choice == "replay":
            if replay_fn is None:
                from . import replay
                replay_fn = replay.run
            return replay_fn(cfg, None)
        raise LabError("Start was cancelled.", "Run dfir-lab start again when ready.")

    # 3. Output folders (never inside evidence) and the worksheet.
    casekit.ensure_output_dirs(cfg)
    casekit.ensure_worksheet(cfg)

    # The course settings must be in place, or Claude Code is not started
    # (spec 6.4).
    try:
        configure_claude.ensure(cfg)
    except LabError as exc:
        raise ConfigError(exc.what + " Claude Code was not started, because it would run "
                          "without the course settings.", exc.todo)
    # First interactive start: pre-accept the trust question for the case
    # folder. If that fails, the student sees the question once.
    trusted, why = configure_claude.trust_case_folder(cfg)
    if not trusted:
        fsutil.log("start.trust-not-seeded", reason=why)
    note = "" if trusted else (
        " If Claude Code asks whether you trust this folder, choose "
        "\"Yes, I trust this folder\" and press Enter.")

    # 4. Banner.
    ui.out(banner(cfg, note))

    # 6. Session log (before exec, which does not return).
    fsutil.append_line(paths.sessions_log(), "%s lab=%s case=%s" % (
        fsutil.utc_iso(), cfg.lab_version, cfg.case_id))
    fsutil.log("start", case=cfg.case_id)

    # 5. Replace this process with Claude Code in the case folder.
    os.chdir(str(cfg.case_dir))
    argv = [exe] + list(passthrough or [])
    env = process_env(cfg)
    if os.environ.get("DFIR_LAB_NO_EXEC") == "1":   # used by the tests
        ui.out("[no-exec] would run: %s in %s" % (" ".join(argv), cfg.case_dir))
        return 0
    os.execve(exe, argv, env)
    return 0  # not reached
