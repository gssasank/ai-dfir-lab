"""Command-line entry point for `dfir-lab`."""

import argparse
import sys

from . import TOOLKIT_VERSION, fsutil, ui
from .errors import EXIT_CONFIG, EXIT_FAIL, EXIT_USAGE, LabError, UsageError
from .redact import redact

GLOBAL_FLAGS = ("--debug", "--no-gui", "--pause")

COMMANDS = [
    ("setup-key", "store and test your personal Duke AI key"),
    ("use", "choose the AI access: your Duke key (default) or your own Claude subscription"),
    ("start", "start Claude Code in the case folder (mounts the disk image first)"),
    ("mount", "mount the case's disk image read-only (--status: show whether it is)"),
    ("unmount", "unmount the case's disk image"),
    ("check", "readiness check; prints a readiness code"),
    ("verify-code", "verify a readiness code against its results file"),
    ("checkpoint", "check an investigation checkpoint answer"),
    ("hint", "show the next hint for a checkpoint"),
    ("progress", "show your checkpoint progress"),
    ("replay", "step through a recorded session (works offline)"),
    ("report-problem", "write a diagnostics file (without your key) for the TA"),
    ("version", "show versions"),
    ("help", "show help"),
]


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError("The command was not typed correctly: %s." % message,
                         "Run: dfir-lab help")

    def _print_message(self, message, file=None):
        if message:
            ui.out(message, end="")


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--debug", action="store_true", default=argparse.SUPPRESS,
                        help="show technical details when something fails")
    common.add_argument("--no-gui", action="store_true", default=argparse.SUPPRESS,
                        help="never open dialog windows; ask in the terminal")
    common.add_argument("--pause", action="store_true", default=argparse.SUPPRESS,
                        help="wait for Enter before exiting (used by desktop shortcuts)")
    p = _Parser(prog="dfir-lab", parents=[common],
                description="CYBERSEC 510 DFIR lab toolkit.",
                epilog="Run 'dfir-lab help <command>' for details on one command.")
    sub = p.add_subparsers(dest="command", parser_class=_Parser, metavar="<command>")
    helps = dict(COMMANDS)

    s = sub.add_parser("setup-key", parents=[common], help=helps["setup-key"],
                       description="Store your personal Duke AI key and test it.")
    s.add_argument("--from-stdin", action="store_true", help="read the key from standard input")
    s.add_argument("--clear", action="store_true", help="delete the stored key")
    s.add_argument("--status", action="store_true",
                   help="say whether a key is stored (shows only its length and last 4 characters)")

    s = sub.add_parser("use", parents=[common], help=helps["use"],
                       description="Choose how Claude Code reaches the AI model. 'duke' (the "
                                   "default) uses your Duke AI Gateway key. 'subscription' "
                                   "uses your own Claude subscription (Pro, Max or higher): "
                                   "no Duke key and no VPN, and it signs you in. Neither "
                                   "choice deletes the key or the sign-in. To sign out of "
                                   "your Claude account: claude auth logout")
    s.add_argument("route", nargs="?", choices=["duke", "subscription"],
                   help="duke or subscription")
    s.add_argument("--status", action="store_true", help="show which one is in use")

    s = sub.add_parser("start", parents=[common], help=helps["start"],
                       description="Check the key and gateway, then start Claude Code in the case "
                                   "folder. Arguments after -- go to Claude Code.")
    s.add_argument("claude_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    s = sub.add_parser("mount", parents=[common], help=helps["mount"],
                       description="Mount the case's disk image read-only and noexec. Safe to "
                                   "run again; nothing is mounted after the VM restarts.")
    s.add_argument("--status", action="store_true",
                   help="only show whether the disk image is mounted (needs no sudo)")
    sub.add_parser("unmount", parents=[common], help=helps["unmount"],
                   description="Unmount the case's disk image.")

    s = sub.add_parser("check", parents=[common], help=helps["check"],
                       description="Run the readiness checks and print a readiness code.")
    s.add_argument("--quick", action="store_true", help="run checks 1 to 5 only")
    s.add_argument("--json", action="store_true", help="print machine-readable results")
    s.add_argument("--only", metavar="N[,N]", help="run only the listed checks, e.g. --only 7,8")

    s = sub.add_parser("verify-code", parents=[common], help=helps["verify-code"])
    s.add_argument("code")
    s.add_argument("--results", required=True, metavar="JSON",
                   help="the readiness-<time>.json file written by 'dfir-lab check'")

    s = sub.add_parser("checkpoint", parents=[common], help=helps["checkpoint"],
                       description="Check an answer, e.g.: dfir-lab checkpoint 1 4625")
    s.add_argument("n", help="checkpoint number")
    s.add_argument("answer", nargs="*", help="your answer")

    s = sub.add_parser("hint", parents=[common], help=helps["hint"])
    s.add_argument("n", help="checkpoint number")

    sub.add_parser("progress", parents=[common], help=helps["progress"])

    s = sub.add_parser("replay", parents=[common], help=helps["replay"])
    s.add_argument("name", nargs="?", help="recording name (default: ask, or the only one)")
    s.add_argument("--list", action="store_true", help="list the recordings")

    sub.add_parser("report-problem", parents=[common], help=helps["report-problem"])
    sub.add_parser("version", parents=[common], help=helps["version"])
    s = sub.add_parser("help", parents=[common], help=helps["help"])
    s.add_argument("topic", nargs="?")
    return p, sub


def _split_passthrough(argv):
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1:]
    return argv, []


def _load_config():
    from .config import Config
    return Config()


def dispatch(args, passthrough, parser, sub):
    cmd = args.command
    if cmd in (None, "help"):
        topic = getattr(args, "topic", None)
        if topic and topic in sub.choices:
            ui.out(sub.choices[topic].format_help())
        else:
            ui.out(parser.format_help())
        return 0
    if cmd == "version":
        try:
            cfg = _load_config()
            lab = cfg.lab_version
        except LabError:
            lab = "(lab.conf not found)"
        ui.out("dfir-lab toolkit %s, lab version %s" % (TOOLKIT_VERSION, lab))
        return 0
    if cmd == "setup-key" and (args.status or args.clear):
        from . import setup_key
        return setup_key.run(None, clear=args.clear, status=args.status)
    if cmd == "verify-code":
        from . import readiness
        ok, why = readiness.verify(args.code, readiness.load_results_file(args.results))
        ui.out("Readiness code %s %s." % (args.code, why))
        return 0 if ok else EXIT_FAIL

    cfg = _load_config()
    if cmd == "setup-key":
        from . import setup_key
        return setup_key.run(cfg, from_stdin=args.from_stdin)
    if cmd == "use":
        from . import use
        return use.run(cfg, args.route, status=args.status)
    if cmd == "start":
        from . import start
        extra = list(passthrough)
        if args.claude_args:
            extra = [a for a in args.claude_args if a != "--"] + extra
        return start.run(cfg, extra)
    if cmd == "mount":
        from . import disk
        return disk.run_mount(cfg, status_only=args.status)
    if cmd == "unmount":
        from . import disk
        return disk.run_unmount(cfg)
    if cmd == "check":
        from . import check
        return check.run(cfg, quick=args.quick, as_json=args.json, only=args.only)
    if cmd == "checkpoint":
        from . import checkpoint
        return checkpoint.run_checkpoint(cfg, args.n, args.answer)
    if cmd == "hint":
        from . import checkpoint
        return checkpoint.run_hint(cfg, args.n)
    if cmd == "progress":
        from . import checkpoint
        return checkpoint.run_progress(cfg)
    if cmd == "replay":
        from . import replay
        return replay.run(cfg, args.name, list_only=args.list)
    if cmd == "report-problem":
        from . import report_problem
        return report_problem.run(cfg)
    raise UsageError("Unknown command %r." % cmd, "Run: dfir-lab help")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    own, passthrough = _split_passthrough(argv)
    flags = {f: (f in own) for f in GLOBAL_FLAGS}
    own = [a for a in own if a not in GLOBAL_FLAGS]
    ui.configure(no_gui=flags["--no-gui"], debug=flags["--debug"])
    code = EXIT_FAIL
    try:
        parser, sub = build_parser()
        args = parser.parse_args(own)
        code = dispatch(args, passthrough, parser, sub)
    except LabError as exc:
        ui.problem(exc.what, exc.todo)
        fsutil.log("error", what=exc.what, code=exc.code)
        code = exc.code
    except KeyboardInterrupt:
        ui.err("")
        ui.err("Stopped.")
        code = EXIT_FAIL
    except SystemExit as exc:   # argparse --help
        code = exc.code if isinstance(exc.code, int) else 0
    except Exception as exc:  # noqa: BLE001 - beginners never see a stack trace
        import traceback
        fsutil.log("crash", error=exc.__class__.__name__, detail=redact(str(exc)))
        if flags["--debug"]:
            ui.err(traceback.format_exc())
        ui.problem("Something unexpected went wrong inside dfir-lab (%s)."
                   % exc.__class__.__name__,
                   "Run  dfir-lab report-problem  and send the file it creates to the TA. "
                   "To see technical details, run the same command again with --debug.")
        code = EXIT_CONFIG
    if flags["--pause"]:
        ui.pause()
    return code
