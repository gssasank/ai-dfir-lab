"""dfir-lab report-problem (spec 5.7): one redacted diagnostics file."""

import platform
import subprocess

from . import check, disk, fsutil, keystore, paths, ui
from .claude_env import find_claude, process_env
from .errors import LabError
from .gateway import Gateway, probe_network


def _os_name():
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def _claude_version(cfg):
    try:
        exe = find_claude(cfg)
    except LabError:
        return "not installed"
    try:
        p = subprocess.run([exe, "--version"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, timeout=20,
                           env=process_env(cfg))
        return (p.stdout or "").strip().splitlines()[0] if p.stdout.strip() else \
            "no output (exit %d)" % p.returncode
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "could not run"


def _network(cfg):
    lines = []
    try:
        base = cfg.base_url
    except LabError as exc:
        return ["gateway: not configured (%s)" % exc.what]
    lines.append("gateway: %s" % base)
    net = probe_network(base, cfg.connect_timeout)
    lines.append("resolved address: %s" % (net.address or "(none)"))
    lines.append("network result: %s%s" % (net.kind, (" - " + net.detail) if net.detail else ""))
    if net.ok:
        lines.append("connect time: %.0f ms" % net.connect_ms)
        if net.tls_version:
            lines.append("TLS: %s" % net.tls_version)
        gw = Gateway.from_config(cfg, keystore.load() or "")
        res = gw.models()
        if res.net_error:
            lines.append("GET /v1/models: no answer (%s)" % res.net_error)
        else:
            lines.append("GET /v1/models: HTTP %d in %.2f s" % (res.status, res.elapsed))
        if gw.last_date:
            lines.append("gateway Date header: %s" % gw.last_date)
    return lines


def run(cfg):
    latest = paths.readiness_dir() / "latest.txt"
    if not latest.is_file():
        ui.out("No readiness results yet; running a quick check first...")
        try:
            check.run(cfg, quick=True)
        except LabError as exc:
            ui.problem(exc.what, exc.todo)
    facts = check.system_facts()
    key = keystore.status()
    sections = [
        "DFIR Lab problem report",
        "This file does NOT contain your Duke AI key. It is safe to send to the TA.",
        "",
        "== Basics ==",
        "created (UTC): %s" % fsutil.utc_iso(),
        "lab version: %s" % cfg.lab_version,
        "case: %s" % cfg.get("case", "id"),
        "OS: %s" % _os_name(),
        "kernel: %s" % platform.release(),
        "architecture: %s" % facts["arch"],
        "hypervisor: %s" % facts["hypervisor"],
        "RAM: %d MB" % facts["ram_mb"],
        "free disk in home: %.1f GB" % facts["free_disk_gb"],
        "CPUs: %d" % facts["cpus"],
        "Claude Code: %s" % _claude_version(cfg),
        "key stored: %s" % ("yes, %d characters, mode %o" % (key["length"], key["mode"])
                            if key["present"] else "no"),
        "",
        "== Network ==",
    ]
    sections += _network(cfg)
    sections += ["", "== Disk image =="]
    try:
        ds = disk.settings(cfg)
        sections += disk.describe(ds, disk.status(ds)) if ds else ["(this case has none)"]
    except LabError as exc:
        sections.append("(not readable: %s)" % exc.what)
    sections += ["", "== Latest check results =="]
    if latest.is_file():
        sections += latest.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        sections.append("(none)")
    sections += ["", "== Last 50 lines of the toolkit log =="]
    sections += fsutil.tail(paths.toolkit_log(), 50) or ["(empty)"]
    text = "\n".join(sections) + "\n"
    out = paths.state_dir() / ("problem-report-%s.txt" % fsutil.utc_stamp())
    fsutil.write_text_atomic(out, text)
    fsutil.log("report-problem", file=out.name)
    ui.out("")
    ui.out("Problem report written to:")
    ui.out("  %s" % out)
    ui.out("It does not contain your key. Send this file to the TA.")
    return 0
