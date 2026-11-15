"""dfir-lab check: the readiness check (spec 5.3)."""

import datetime as _dt
import email.utils
import json
import os
import platform
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import (casekit, configure_claude, disk, fsutil, keystore, paths, readiness,
               route as route_mod, subscription, ui)
from .claude_env import find_claude, process_env, require_min_version
from .errors import ConfigError, LabError, UsageError
from .gateway import (Gateway, KeyLimits, classify, model_ids, probe_network, similar_models,
                      usage_tokens)
from .messages import CANNOT_REACH, outcome_fields, render
from .redact import redact

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
# "Not applicable": a check the student's route does not use (the gateway
# checks 6-9 on the subscription route). Not a failure.
NA = readiness.NA
CLOCK_WARN_S = 300
TOOL_TIMEOUT_S = 20
BUFFER_GAP_S = 0.05

READINESS_PROMPT = (
    "This is an automated readiness test of the DFIR lab. Use the Bash tool to run "
    "exactly this command, once: sh readiness.sh\n"
    "Then reply with the single word DONE. Do not run any other command and do not "
    "read or edit any file."
)
READINESS_ALLOWED_TOOL = "Bash(sh readiness.sh)"


@dataclass
class Result:
    id: int
    name: str
    status: str
    result: str
    todo: str = ""
    data: dict = field(default_factory=dict)


class Ctx:
    def __init__(self, cfg, route=None):
        self.cfg = cfg
        self.route = route_mod.resolve(route)
        self.auth = None
        self._key = None
        self._gw = None
        self.net = None
        self.date_header = None
        self.tokens_in = 0
        self.tokens_out = 0
        self.results = {}

    @property
    def key(self):
        if self._key is None:
            self._key = keystore.load() or ""
        return self._key

    @property
    def gw(self):
        if self._gw is None:
            self._gw = Gateway.from_config(self.cfg, self.key)
        return self._gw

    def add_tokens(self, pair):
        self.tokens_in += pair[0]
        self.tokens_out += pair[1]

    def note_date(self):
        if self._gw is not None and self._gw.last_date:
            self.date_header = self._gw.last_date

    def key_limits(self):
        """The key's limits from /key/info, fetched once (None if unavailable)."""
        if not hasattr(self, "_limits"):
            try:
                self._limits = self.gw.key_info() if self.key else None
            except Exception:  # noqa: BLE001 - informational only
                self._limits = None
        return self._limits

    def failure(self, cid, name, outcome, **extra):
        if outcome.kind in ("rate_limit", "budget", "limit"):
            outcome.limits = (outcome.limits or KeyLimits()).merge(self.key_limits())
        f = outcome_fields(outcome, self.net)
        f.setdefault("host", self.gw.host)
        f.setdefault("port", self.gw.port)
        if not f.get("host"):
            f["host"], f["port"] = self.gw.host, self.gw.port
        f.update(extra)
        what, todo = render(outcome.kind, self.cfg, **f)
        return Result(cid, name, FAIL, what, todo, {"outcome": outcome.kind,
                                                     "status": outcome.status})


# --------------------------------------------------------------------------
# 1. System
# --------------------------------------------------------------------------

def _ram_mb():
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError, AttributeError):
        return 0


def hypervisor():
    exe = shutil.which("systemd-detect-virt")
    if not exe:
        return "unknown"
    try:
        p = subprocess.run([exe], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, timeout=5)
        return (p.stdout or "").strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def system_facts():
    du = shutil.disk_usage(str(paths.home()))
    return {
        "arch": platform.machine(),
        "ram_mb": _ram_mb(),
        "free_disk_gb": round(du.free / (1024 ** 3), 1),
        "cpus": os.cpu_count() or 0,
        "hypervisor": hypervisor(),
        "kernel": platform.release(),
    }


def check_system(ctx):
    cfg = ctx.cfg
    f = system_facts()
    need_ram = cfg.get_int("requirements", "min_ram_mb")
    need_disk = cfg.get_float("requirements", "min_free_disk_gb")
    need_cpu = cfg.get_int("requirements", "min_cpus")
    summary = "%s, %d MB RAM, %.1f GB free, %d CPUs, hypervisor: %s" % (
        f["arch"], f["ram_mb"], f["free_disk_gb"], f["cpus"], f["hypervisor"])
    fails, warns = [], []
    if f["arch"] not in ("x86_64", "AMD64", "amd64"):
        fails.append("this computer is %s, but the lab VM needs x86-64 (Intel/AMD)" % f["arch"])
    if f["free_disk_gb"] < need_disk:
        fails.append("only %.1f GB of disk space is free; at least %g GB is needed"
                     % (f["free_disk_gb"], need_disk))
    if f["ram_mb"] < need_ram:
        warns.append("the VM has %d MB of memory; %d MB is recommended" % (f["ram_mb"], need_ram))
    if f["cpus"] < need_cpu:
        warns.append("the VM has %d CPU(s); %d are recommended" % (f["cpus"], need_cpu))
    year = fsutil.utc_now().year
    if year < 2025:
        warns.append("the VM clock says the year is %d" % year)
    if fails:
        return Result(1, "System", FAIL, summary + ". Problem: " + "; ".join(fails + warns) + ".",
                      "Shut down the VM, give it more disk space in your VM program's settings "
                      "(or free some space), then start it again. If your laptop is not "
                      "x86-64, work with a partner whose laptop is.", f)
    if warns:
        return Result(1, "System", WARN, summary + ". " + "; ".join(warns).capitalize() + ".",
                      "The lab will work but may be slow. If you can, shut down the VM and "
                      "raise its memory/CPUs in your VM program's settings. Fix the clock "
                      "with: sudo timedatectl set-ntp true", f)
    return Result(1, "System", PASS, summary, "", f)


def amend_clock(ctx):
    r = ctx.results.get(1)
    if r is None or not ctx.date_header or r.status == FAIL:
        return
    try:
        server = email.utils.parsedate_to_datetime(ctx.date_header)
    except (TypeError, ValueError):
        return
    if server.tzinfo is None:
        server = server.replace(tzinfo=_dt.timezone.utc)
    skew = (fsutil.utc_now() - server).total_seconds()
    r.data["clock_skew_s"] = round(skew)
    if abs(skew) > CLOCK_WARN_S:
        r.status = WARN
        r.data["clock_amended"] = True
        r.result += ". The VM clock is %d minutes %s the gateway's clock" % (
            abs(skew) // 60, "ahead of" if skew > 0 else "behind")
        r.todo = ("Fix the VM clock: run  sudo timedatectl set-ntp true  then restart the VM. "
                  + (r.todo or ""))
    else:
        r.result += ", clock OK"


# --------------------------------------------------------------------------
# 2. Tools
# --------------------------------------------------------------------------

def parse_tools(path):
    tools = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if "\t" in line:
                cmd, args = line.split("\t", 1)
            else:
                parts = line.split(None, 1)
                cmd, args = parts[0], (parts[1] if len(parts) > 1 else "")
            tools.append((cmd.strip(), args.strip()))
    return tools


def check_tools(ctx):
    path = ctx.cfg.case_path("tools_file")
    if not path or not path.is_file():
        return Result(2, "Tools", FAIL, "The case tools list %s is missing." % path,
                      "The case kit is incomplete. Tell the TA.")
    tools = parse_tools(path)
    missing, broken, slow, ok = [], [], [], []
    for cmd, args in tools:
        exe = shutil.which(cmd)
        if not exe:
            missing.append(cmd)
            continue
        try:
            p = subprocess.run([exe] + shlex.split(args), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=TOOL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            slow.append(cmd)
            continue
        except OSError:
            broken.append(cmd)
            continue
        if p.returncode in (126, 127):
            broken.append(cmd)
        else:
            ok.append(cmd)
    data = {"ok": ok, "missing": missing, "broken": broken, "slow": slow}
    if missing or broken or slow:
        bits = []
        if missing:
            bits.append("not installed: " + ", ".join(missing))
        if broken:
            bits.append("will not run: " + ", ".join(broken))
        if slow:
            bits.append("did not answer within %ds: " % TOOL_TIMEOUT_S + ", ".join(slow))
        return Result(2, "Tools", FAIL, "%d of %d tools work; %s." % (len(ok), len(tools),
                                                                     "; ".join(bits)),
                      "The VM image is missing forensic tools. Tell the TA which ones.", data)
    return Result(2, "Tools", PASS, "all %d case tools found and working" % len(tools), "", data)


# --------------------------------------------------------------------------
# 3. Case files
# --------------------------------------------------------------------------

_RANK = {PASS: 0, SKIP: 0, NA: 0, WARN: 1, FAIL: 2}


def check_case(ctx):
    """Check 3: the case kit and overlay files against the manifest and, when
    the case has one, the disk image (segment hashes, read-only noexec mount,
    a known system file readable through it)."""
    r = _check_kit(ctx)
    part = disk_part(ctx.cfg)
    if part is None:
        return r
    status, text, todo = part
    r.data["disk"] = {"status": status, "result": text}
    r.result = "%s; %s" % (r.result.rstrip("."), text)
    if _RANK[status] > _RANK[r.status]:
        r.status = status
    r.todo = " ".join(t for t in (r.todo, todo) if t)
    return r


def disk_part(cfg):
    """(status, text, todo) for the case's disk image, or None if it has none."""
    ds = disk.settings(cfg)
    if ds is None:
        return None
    st = disk.status(ds)
    if st.missing:
        return (FAIL, "the disk image is not installed (missing: %s)" % ", ".join(st.missing[:3]),
                disk.missing_todo(ds))
    try:
        bad, writable = disk.verify_segments(ds)
    except OSError as exc:
        return (FAIL, "the disk image could not be read (%s)" % (exc.strerror or exc),
                "Run the lab setup command again; if that does not help, tell the TA.")
    if bad:
        return (FAIL, "the disk image does not match its expected SHA-256 (%s)" % ", ".join(bad),
                "The disk image on this VM is damaged. Download %s again from the course site "
                "into ~/Downloads and run the lab setup command again." % ds.zip_name)
    try:
        disk.mount(ds, quiet=True)
    except LabError as exc:
        return FAIL, "the disk image could not be mounted: %s" % exc.what, exc.todo or ""
    text = ("disk image: %d files match their SHA-256, mounted read-only and noexec at %s, "
            "%s readable" % (len(ds.segments), ds.mount_point, ds.probe))
    if writable:
        return (WARN, text + ", but your account can change %s" % ", ".join(writable),
                "Nothing breaks now, but tell the TA so the setup can be fixed.")
    return PASS, text, ""


def _check_kit(ctx):
    cfg = ctx.cfg
    case_dir = cfg.case_dir
    if not case_dir.is_dir():
        return Result(3, "Case files", FAIL, "The case folder %s does not exist." % case_dir,
                      "The case kit is not installed. Tell the TA.")
    mpath = cfg.case_path("manifest")
    if not mpath or not mpath.is_file():
        return Result(3, "Case files", FAIL, "The case manifest %s is missing." % mpath,
                      "The case kit is incomplete. Tell the TA.")
    try:
        entries = casekit.parse_manifest(mpath)
    except LabError as exc:
        return Result(3, "Case files", FAIL, exc.what, exc.todo)
    missing, changed, writable = [], [], []
    for digest, rel in entries:
        p = case_dir / rel
        if not p.is_file():
            missing.append(rel)
            continue
        try:
            if casekit.sha256_file(p) != digest:
                changed.append(rel)
        except OSError:
            missing.append(rel)
            continue
        if os.access(p, os.W_OK):
            writable.append(rel)
    for d in casekit.evidence_dirs(case_dir, entries):
        if os.access(d, os.W_OK):
            writable.append(str(d.relative_to(case_dir)) + "/")
    data = {"files": len(entries), "missing": missing, "changed": changed, "writable": writable}
    if missing or changed:
        bits = []
        if missing:
            bits.append("%d missing (%s)" % (len(missing), ", ".join(missing[:3])))
        if changed:
            bits.append("%d changed (%s)" % (len(changed), ", ".join(changed[:3])))
        return Result(3, "Case files", FAIL,
                      "Evidence does not match the case manifest: %s." % "; ".join(bits),
                      "The evidence is damaged or incomplete, so results would differ from "
                      "everyone else's. Tell the TA; you may need a fresh copy of the VM.", data)
    if writable:
        return Result(3, "Case files", WARN,
                      "All %d evidence files match, but your account can change some of them "
                      "(%s)." % (len(entries), ", ".join(writable[:3])),
                      "Nothing breaks now, but be careful not to modify evidence. Tell the TA "
                      "so the image can be fixed.", data)
    return Result(3, "Case files", PASS,
                  "all %d evidence files match the manifest and are read-only" % len(entries),
                  "", data)


# --------------------------------------------------------------------------
# 4. Key
# --------------------------------------------------------------------------

def check_key(ctx):
    st = keystore.status()
    if not st["present"]:
        return Result(4, "Key", FAIL, "No Duke AI key is stored.",
                      "Run: dfir-lab setup-key   (paste the key from the Duke AI dashboard)")
    if st.get("problems"):
        return Result(4, "Key", FAIL, "The stored key looks wrong: " + " ".join(st["problems"]),
                      "Run: dfir-lab setup-key   and paste the whole key again.")
    if st["mode"] != 0o600 or not st.get("owner_ok", True):
        return Result(4, "Key", FAIL,
                      "The key file %s can be read by other accounts (mode %o)." % (
                          st["path"], st["mode"]),
                      "Run: dfir-lab setup-key   (it stores the key again with safe "
                      "permissions), or: chmod 600 %s" % st["path"])
    return Result(4, "Key", PASS, "stored, %d characters, ends in ...%s, private (0600)"
                  % (st["length"], st["last4"]))


# --------------------------------------------------------------------------
# 5. Network
# --------------------------------------------------------------------------

def check_network(ctx):
    cfg = ctx.cfg
    net = probe_network(cfg.base_url, cfg.connect_timeout)
    ctx.net = net
    data = {"host": net.host, "port": net.port, "address": net.address,
            "connect_ms": round(net.connect_ms, 1), "kind": net.kind}
    if not net.ok:
        what, todo = render(net.kind, cfg, host=net.host, port=net.port, detail=net.detail)
        return Result(5, "Network", FAIL, what, todo, data)
    tls = (", TLS %s OK" % net.tls_version) if net.scheme == "https" else ", plain HTTP (no TLS)"
    return Result(5, "Network", PASS, "%s resolves to %s, connected in %.0f ms%s" % (
        net.host, net.address, net.connect_ms, tls), "", data)


# --------------------------------------------------------------------------
# 4-5 on the subscription route: signed in, Anthropic reachable
# --------------------------------------------------------------------------

def check_signin(ctx):
    """Check 4 (subscription route): Claude Code is signed in with a Claude
    subscription. Asks Claude Code itself (`claude auth status --json`); the
    sign-in file is never opened."""
    cfg, name = ctx.cfg, "Sign-in"
    try:
        exe = find_claude(cfg)
    except ConfigError as exc:
        return Result(4, name, FAIL, exc.what, exc.todo)
    try:
        configure_claude.ensure(cfg, quiet=True, route=ctx.route)
    except LabError as exc:
        return Result(4, name, FAIL, exc.what, exc.todo)
    st = subscription.auth_status(cfg, exe)
    ctx.auth = st
    data = {"method": st.method, "plan": st.plan, "expires": st.expires}
    if not st.ok:
        return Result(4, name, FAIL, "Could not ask Claude Code whether it is signed in: %s."
                      % st.detail, "Run the check again. If it fails again, run dfir-lab "
                      "report-problem and send the file to the TA.", data)
    if st.expired:
        what, todo = render("sub_login_expired", cfg,
                            detail="the sign-in ended on %s" % st.expires)
        return Result(4, name, FAIL, what, todo, data)
    if not st.signed_in:
        kind = "sub_wrong_login" if st.logged_in else "sub_not_signed_in"
        what, todo = render(kind, cfg, detail=subscription.describe(st))
        return Result(4, name, FAIL, what, todo, data)
    text = "%s; Claude Code will use %s" % (subscription.describe(st),
                                             subscription.model_text(cfg))
    if st.ends_soon:
        return Result(4, name, WARN, text + "; the sign-in ends within a day (%s)" % st.expires,
                      "Sign in again before the lab: dfir-lab use subscription", data)
    return Result(4, name, PASS, text, "", data)


def check_anthropic(ctx):
    """Check 5 (subscription route): Anthropic's service, reached directly from
    the VM (no VPN): the API, the sign-in token service and the sign-in pages."""
    cfg = ctx.cfg
    timeout = cfg.connect_timeout
    parts, data = [], {"hosts": []}
    for url in cfg.subscription_hosts:
        net = probe_network(url, timeout)
        data["hosts"].append({"host": net.host, "kind": net.kind, "address": net.address,
                              "connect_ms": round(net.connect_ms, 1)})
        if not net.ok:
            what, todo = render("sub_no_network", cfg, **subscription.net_fields(net))
            return Result(5, "Network", FAIL, what, todo, data)
        parts.append("%s (%.0f ms, TLS %s)" % (net.host, net.connect_ms, net.tls_version or "?"))
    return Result(5, "Network", PASS, "Anthropic's service reachable directly, no VPN needed: "
                  + ", ".join(parts), "", data)


def not_applicable(cid, name):
    def fn(ctx):
        return Result(cid, name, NA, "not applicable on the subscription route (it does not "
                      "use the Duke gateway)")
    return fn


# --------------------------------------------------------------------------
# 6-9. Gateway
# --------------------------------------------------------------------------

def check_login(ctx):
    cfg = ctx.cfg
    if not ctx.key:
        return Result(6, "Gateway login", FAIL, "No key is stored, so login cannot be tested.",
                      "Run: dfir-lab setup-key")
    res = ctx.gw.models()
    ctx.note_date()
    out = classify(res, cfg.model_main, route="models")
    data = {"status": res.status, "outcome": out.kind}
    if out.kind == "models_404":
        what, todo = render("models_404", cfg)
        return Result(6, "Gateway login", WARN, what, todo, data)
    if out.kind != "ok":
        return ctx.failure(6, "Gateway login", out)
    ids = model_ids(res)
    if ids is None:
        # A 200 that is not a model list proves nothing about the key: it can
        # be a Wi-Fi/VPN login page or a proxy (review M2).
        body = res.json()
        if not isinstance(body, dict):
            data["outcome"] = "unexpected_page"
            what, todo = render("unexpected_page", cfg, status=res.status,
                                body=_snippet(res.text))
            return Result(6, "Gateway login", FAIL, what, todo, data)
        return Result(6, "Gateway login", WARN,
                      "The gateway answered HTTP 200, but not with a model list in a format "
                      "dfir-lab knows, so your key could not be confirmed here.",
                      "Nothing to do unless the next checks fail; check 7 tests the key and "
                      "model directly.", data)
    data["models"] = ids[:50]
    if cfg.model_main not in ids:
        sim = similar_models(cfg.model_main, ids)
        similar = (" Similar models your key can use: %s." % ", ".join(sim)) if sim else ""
        what, todo = render("model_missing", cfg, similar=similar)
        data["outcome"] = "model_missing"
        return Result(6, "Gateway login", WARN, what, todo, data)
    limits = ctx.key_limits()
    text = ""
    if limits is not None and limits.summary():
        text = "; key limits: %s" % limits.summary()
        data["limits"] = {k: getattr(limits, k) for k in ("rpm", "tpm", "max_budget",
                                                          "budget_duration", "budget_reset_at",
                                                          "spend")}
    return Result(6, "Gateway login", PASS,
                  "key accepted; %d models available, including %s%s" % (
                      len(ids), cfg.model_main, text), "", data)


def _snippet(text, n=300):
    """Redact first, then shorten: a key cut in half by truncation would
    otherwise slip past the redactor."""
    return " ".join(redact(text or "").split())[:n]


def _text_payload(cfg, prompt, max_tokens):
    # A fresh nonce in every prompt: the Duke gateway caches responses, and a
    # repeated identical request comes back from that cache (a replayed
    # stream arrives all at once, with a ping first), which tests nothing.
    return {"model": cfg.model_main, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": "%s (check %s)" % (
                prompt, os.urandom(4).hex())}]}


def check_model(ctx):
    cfg = ctx.cfg
    if not ctx.key:
        return Result(7, "Model answers", FAIL, "No key is stored.", "Run: dfir-lab setup-key")
    res = ctx.gw.messages(_text_payload(cfg, "Reply with the single word READY.", 16))
    ctx.note_date()
    out = classify(res, cfg.model_main, route="messages")
    if out.kind != "ok":
        return ctx.failure(7, "Model answers", out)
    data = res.json()
    if not (isinstance(data, dict) and data.get("type") == "message"
            and isinstance(data.get("content"), list)):
        what, todo = render("bad_reply", cfg, detail="no Anthropic 'message' object")
        return Result(7, "Model answers", FAIL, what, todo)
    ctx.add_tokens(usage_tokens(data))
    text = "".join(b.get("text", "") for b in data["content"]
                   if isinstance(b, dict) and b.get("type") == "text"
                   and isinstance(b.get("text"), str)).strip()
    if not text:
        what, todo = render("bad_reply", cfg, detail="the reply contained no text")
        return Result(7, "Model answers", FAIL, what, todo, {"tokens": usage_tokens(data)})
    return Result(7, "Model answers", PASS, "%s answered in %.1f s: %r" % (
        data.get("model") or cfg.model_main, res.elapsed, _snippet(text, 40)), "",
        {"tokens": usage_tokens(data)})


def check_stream(ctx):
    cfg = ctx.cfg
    payload = _text_payload(cfg, "Count from 1 to 40, separated by spaces. Output only the "
                                 "numbers.", 200)
    res = ctx.gw.stream_messages(payload)
    ctx.note_date()
    if res.net_error or res.status != 200:
        if res.status == 0 and not res.net_error and res.dropped:
            what, todo = render("stream_dropped", cfg, detail=res.net_detail or "connection closed")
            return Result(8, "Streaming", FAIL, what, todo)
        return ctx.failure(8, "Streaming", classify(res, cfg.model_main, route="messages"))
    # Anthropic streams may carry "ping" events anywhere (the gateway's
    # response cache even replays one first); they are not part of the message.
    events = [e for e in res.events if e[0] != "ping"]
    names = [e[0] for e in events]
    deltas = sum(1 for n in names if n == "content_block_delta")
    tin = tout = 0
    for name, d, _ in events:
        if name == "message_start" and isinstance(d, dict):
            a, b = usage_tokens(d.get("message", {}))
            tin += a
            tout += b
        elif name == "message_delta" and isinstance(d, dict):
            tout = max(tout, usage_tokens(d)[1])
    data = {"events": len(names), "deltas": deltas, "reads": res.reads}
    if res.dropped or not res.complete:
        what, todo = render("stream_dropped", cfg, detail="%d events arrived, no message_stop"
                            % len(names))
        return Result(8, "Streaming", FAIL, what, todo, data)
    if not names or names[0] != "message_start" or names[-1] != "message_stop":
        what, todo = render("bad_reply", cfg, detail="streamed events out of order: %s ... %s"
                            % (names[:1], names[-1:]))
        return Result(8, "Streaming", FAIL, what, todo, data)
    # Every event must carry a JSON object whose type matches the event name
    # (review M2: empty or invalid events used to pass).
    invalid = [n for n, d, _ in events if not isinstance(d, dict) or d.get("type") != n]
    if invalid:
        what, todo = render("bad_reply", cfg, detail="%d of %d streamed events were empty or "
                            "not valid JSON (first: %s)" % (len(invalid), len(names), invalid[0]))
        return Result(8, "Streaming", FAIL, what, todo, data)
    text_deltas = [d for n, d, _ in events if n == "content_block_delta"
                   and isinstance(d.get("delta"), dict) and d["delta"].get("type") == "text_delta"
                   and d["delta"].get("text")]
    if not text_deltas:
        what, todo = render("bad_reply", cfg, detail="the streamed reply contained no text")
        return Result(8, "Streaming", FAIL, what, todo, data)
    ctx.add_tokens((tin, tout))
    gap = (events[-1][2] if events else 0) - (events[0][2] if events else 0)
    data["spread_s"] = round(gap, 3)
    if "x-litellm-cache-key" in res.headers:
        # Served from the gateway's response cache: it says nothing about
        # whether live replies stream, so do not judge buffering from it.
        return Result(8, "Streaming", WARN,
                      "The gateway answered from its response cache, so live streaming could "
                      "not be tested this time (%d events)." % len(names),
                      "Run the check again.", data)
    if deltas >= 3 and gap < BUFFER_GAP_S:
        what, todo = render("stream_buffered", cfg)
        return Result(8, "Streaming", FAIL, what, todo, data)
    if deltas < 3:
        return Result(8, "Streaming", WARN,
                      "Streaming works, but the reply was too short (%d text pieces) to tell "
                      "whether a proxy holds streamed replies back." % deltas,
                      "Run the check again. If this keeps appearing and Claude Code stalls on "
                      "long answers, %s." % ctx.cfg.contact_line(), data)
    return Result(8, "Streaming", PASS, "%d events from message_start to message_stop over "
                  "%.2f s" % (len(names), gap), "", data)


CHECK_TOOL = {
    "name": "get_case_clock",
    "description": "Returns the current time on the case clock for a time zone. You must call "
                   "this tool to answer any question about the case clock.",
    "input_schema": {
        "type": "object",
        "properties": {"timezone": {"type": "string", "description": "Time zone, e.g. UTC"}},
        "required": ["timezone"],
    },
}


def check_tool_use(ctx):
    cfg = ctx.cfg
    payload = _text_payload(cfg, "What time is it on the case clock in UTC? Use the "
                                 "get_case_clock tool.", 200)
    payload["tools"] = [CHECK_TOOL]
    # "auto", not "any": Claude Sonnet 5.5, Opus 5.5 and Fable 5.1/5.2 reject a forced
    # tool_choice with HTTP 400 (seen on the Duke gateway, 2026-11-14). The prompt
    # asks for the tool; Claude Code itself always sends "auto".
    payload["tool_choice"] = {"type": "auto"}
    res = ctx.gw.messages(payload)
    ctx.note_date()
    out = classify(res, cfg.model_main, route="messages")
    if out.kind != "ok":
        return ctx.failure(9, "Tool use", out)
    data = res.json() or {}
    ctx.add_tokens(usage_tokens(data))
    blocks = [b for b in data.get("content", []) if isinstance(b, dict)
              and b.get("type") == "tool_use"]
    if data.get("stop_reason") != "tool_use" or not blocks:
        what, todo = render("bad_reply", cfg, detail="the model did not call the tool "
                            "(stop_reason=%s)" % data.get("stop_reason"))
        return Result(9, "Tool use", FAIL, what, todo)
    b = blocks[0]
    inp = b.get("input")
    if b.get("name") != CHECK_TOOL["name"] or not isinstance(inp, dict) \
            or not isinstance(inp.get("timezone"), str):
        what, todo = render("bad_reply", cfg, detail="the tool call had invalid input")
        return Result(9, "Tool use", FAIL, what, todo)
    return Result(9, "Tool use", PASS, "the model called the tool with valid input %s"
                  % _snippet(json.dumps(inp), 60))


# --------------------------------------------------------------------------
# 10. Claude Code task
# --------------------------------------------------------------------------

def _sample_dir():
    return paths.share_dir() / "readiness-sample"


def _kill_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, AttributeError):
        try:
            proc.kill()
        except OSError:
            pass


def check_claude(ctx):
    cfg = ctx.cfg
    name = "Claude Code task"
    try:
        exe = find_claude(cfg)
        require_min_version(cfg, exe)
    except ConfigError as exc:
        return Result(10, name, FAIL, exc.what, exc.todo)
    # The course settings must be in place, or Claude Code is not run (spec 6.4).
    try:
        configure_claude.ensure(cfg, quiet=True, route=ctx.route)
    except LabError as exc:
        return Result(10, name, FAIL, exc.what + " Claude Code was not run, because it would "
                      "run without the course settings.", exc.todo)
    sample = _sample_dir()
    if not (sample / "readiness.sh").is_file():
        return Result(10, name, FAIL, "The readiness sample folder %s is missing." % sample,
                      "The toolkit is not installed completely. Tell the TA.")
    max_turns = cfg.get_int("claude", "max_turns_readiness")
    limit_s = cfg.get_float("claude", "readiness_timeout_s")
    tmp = Path(tempfile.mkdtemp(prefix="dfir-readiness-"))
    try:
        for item in sample.iterdir():
            if item.is_file():
                shutil.copyfile(item, tmp / item.name)
        nonce = os.urandom(8).hex()
        with open(tmp / "sample.txt", "a", encoding="utf-8") as fh:
            fh.write("readiness nonce %s\n" % nonce)
        expected = casekit.sha256_file(tmp / "sample.txt")
        cmd = [exe, "-p", READINESS_PROMPT, "--max-turns", str(max_turns),
               "--output-format", "json", "--permission-mode", "dontAsk",
               "--allowedTools", READINESS_ALLOWED_TOOL, "--no-session-persistence"]
        ui.debug("running Claude Code: %s" % " ".join(shlex.quote(c) for c in cmd))
        start = time.monotonic()
        proc = subprocess.Popen(cmd, cwd=str(tmp), env=process_env(cfg, route=ctx.route),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=limit_s)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            proc.communicate()
            return Result(10, name, FAIL,
                          "Claude Code did not finish the test task within %g seconds." % limit_s,
                          "%s Try again; if it fails twice, run dfir-lab report-problem and "
                          "send the file to the TA." % (
                              "Anthropic's service may be slow or a reply stalled."
                              if ctx.route == route_mod.SUBSCRIPTION else
                              "The gateway may be slow or a reply stalled."))
        elapsed = time.monotonic() - start
        # Redact whole outputs before picking lines out of them.
        out_text = redact(stdout.decode("utf-8", errors="replace"))
        err_text = redact(stderr.decode("utf-8", errors="replace"))
        info = {}
        try:
            info = json.loads(out_text.strip().splitlines()[-1]) if out_text.strip() else {}
        except (ValueError, IndexError):
            info = {}
        if isinstance(info, dict):
            ctx.add_tokens(usage_tokens(info))
        data = {"exit": proc.returncode, "turns": info.get("num_turns") if isinstance(info, dict)
                else None, "seconds": round(elapsed, 1)}
        result_file = tmp / "analysis" / "readiness.txt"
        content = ""
        if result_file.is_file():
            content = result_file.read_text(encoding="utf-8", errors="replace")
        if expected in content:
            return Result(10, name, PASS, "Claude Code ran the command and saved the result "
                          "(%s turns, %.0f s)" % (data["turns"] or "?", elapsed), "", data)
        detail = ""
        if isinstance(info, dict):
            detail = str(info.get("result") or info.get("subtype") or "")
        if not detail:
            lines = [l for l in (err_text or out_text).splitlines() if l.strip()]
            detail = lines[-1] if lines else "no output"
        detail = redact(detail.strip())[:300]
        if ctx.route == route_mod.SUBSCRIPTION:
            kind = subscription.classify_text("%s\n%s" % (detail, err_text[-2000:]))
            if kind:
                data["outcome"] = kind
                what, todo = render(kind, cfg, detail=detail, host="api.anthropic.com",
                                    reason="could not be reached")
                return Result(10, name, FAIL, what, todo, data)
        if isinstance(info, dict) and "max_turns" in str(info.get("subtype", "")):
            what = "Claude Code used up its %d-turn limit without finishing the test task." % max_turns
        elif result_file.is_file():
            what = "Claude Code wrote analysis/readiness.txt, but its content is wrong."
        else:
            what = "Claude Code did not complete the test task (exit code %s): %s" % (
                proc.returncode, detail)
        return Result(10, name, FAIL, what,
                      "Run the check again. If it fails again, run dfir-lab report-problem "
                      "and send the file to the TA.", data)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 11. Results
# --------------------------------------------------------------------------

def _probe_write(d):
    try:
        fd, p = tempfile.mkstemp(dir=str(d), prefix=".dfir-write-test-")
        os.close(fd)
        os.unlink(p)
        return True
    except OSError:
        return False


def check_results(ctx):
    cfg = ctx.cfg
    dirs = [paths.state_dir(), paths.readiness_dir(), paths.logs_dir()]
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    try:
        casekit.ensure_output_dirs(cfg)
    except LabError as exc:
        return Result(11, "Results", FAIL, exc.what, exc.todo)
    dirs += [cfg.case_dir / n for n in casekit.OUTPUT_DIRS]
    bad = [str(d) for d in dirs if not _probe_write(d)]
    if bad:
        return Result(11, "Results", FAIL, "Cannot save files in: %s." % ", ".join(bad),
                      "Tell the TA; these folders must be writable by your account.")
    free = min(shutil.disk_usage(str(d)).free for d in dirs) / (1024 ** 3)
    if free < 0.2:
        return Result(11, "Results", FAIL, "Only %.2f GB free where results are saved." % free,
                      "Delete large files you do not need, or give the VM more disk space.")
    if free < 1:
        return Result(11, "Results", WARN, "Only %.2f GB free where results are saved." % free,
                      "Delete large files you do not need, or give the VM more disk space.")
    return Result(11, "Results", PASS, "results folders are writable, %.1f GB free" % free)


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

CHECKS = [
    (1, "System", check_system, ()),
    (2, "Tools", check_tools, ()),
    (3, "Case files", check_case, ()),
    (4, "Key", check_key, ()),
    (5, "Network", check_network, ()),
    (6, "Gateway login", check_login, (4, 5)),
    (7, "Model answers", check_model, (4, 5, 6)),
    (8, "Streaming", check_stream, (7,)),
    (9, "Tool use", check_tool_use, (7,)),
    (10, "Claude Code task", check_claude, (7, 8, 9)),
    (11, "Results", check_results, ()),
]
NAMES = {cid: name for cid, name, _, _ in CHECKS}

# The optional subscription route: same numbers, so the readiness code, the
# setup script and the documents line up. 4 and 5 become the sign-in and
# Anthropic's service; 6-9 talk to the Duke gateway with the key and are not
# applicable; 10 is the same real Claude Code task.
SUBSCRIPTION_CHECKS = [
    (1, "System", check_system, ()),
    (2, "Tools", check_tools, ()),
    (3, "Case files", check_case, ()),
    (4, "Sign-in", check_signin, ()),
    (5, "Network", check_anthropic, ()),
    (6, "Gateway login", not_applicable(6, "Gateway login"), ()),
    (7, "Model answers", not_applicable(7, "Model answers"), ()),
    (8, "Streaming", not_applicable(8, "Streaming"), ()),
    (9, "Tool use", not_applicable(9, "Tool use"), ()),
    (10, "Claude Code task", check_claude, (4, 5)),
    (11, "Results", check_results, ()),
]


def checks_for(route):
    return SUBSCRIPTION_CHECKS if route == route_mod.SUBSCRIPTION else CHECKS


def names_for(route):
    return {cid: name for cid, name, _, _ in checks_for(route)}


def parse_only(text):
    try:
        ids = sorted({int(x) for x in text.replace(" ", "").split(",") if x})
    except ValueError:
        raise UsageError("--only needs check numbers such as 7 or 7,8 (got %r)." % text,
                         "Run: dfir-lab check --only 7,8")
    bad = [i for i in ids if i not in NAMES]
    if bad or not ids:
        raise UsageError("There is no check number %s; checks are numbered 1 to 11."
                         % ",".join(map(str, bad or ids)), "Run: dfir-lab check --help")
    return ids


def run_checks(cfg, selected, on_result=None, route=None):
    """Run the selected checks for a route (default: the stored route)."""
    ctx = Ctx(cfg, route)
    names = names_for(ctx.route)
    for cid, name, fn, deps in checks_for(ctx.route):
        if cid not in selected:
            continue
        blocker = next((d for d in deps if d in ctx.results and ctx.results[d].status in
                        (FAIL, SKIP)), None)
        if blocker is not None:
            r = Result(cid, name, SKIP, "skipped because check %d (%s) did not pass"
                       % (blocker, names[blocker]))
        else:
            try:
                r = fn(ctx)
            except LabError as exc:
                r = Result(cid, name, FAIL, exc.what, exc.todo or "")
            except Exception as exc:  # noqa: BLE001 - one broken check must not hide the rest
                if ui.debug_enabled():
                    import traceback
                    ui.err(traceback.format_exc())
                r = Result(cid, name, FAIL, "The check itself crashed (%s)." %
                           exc.__class__.__name__,
                           "Run dfir-lab report-problem and send the file to the TA.")
        r.result, r.todo = redact(r.result), redact(r.todo)
        ctx.results[cid] = r
        if on_result:
            on_result(r)
    amend_clock(ctx)
    return ctx


def _shown_status(status):
    return "N/A" if status == NA else status


def _line(r):
    return "%2d  %-17s %-4s  %s" % (r.id, r.name, _shown_status(r.status), r.result)


def _route_line(route):
    if route == route_mod.SUBSCRIPTION:
        return "AI access   : your own Claude subscription (checks 6-9 do not apply)"
    return "AI access   : your Duke AI Gateway key"


def build_report(cfg, ctx, code, ready, partial, when):
    sub = ctx.route == route_mod.SUBSCRIPTION
    lines = [
        "DFIR Lab readiness report",
        "This report does not contain your Claude sign-in or any key." if sub else
        "This report does not contain your Duke AI key.",
        "",
        "Lab version : %s" % cfg.lab_version,
        "Course      : %s" % cfg.course,
        "Case        : %s" % cfg.get("case", "id"),
        _route_line(ctx.route),
        "Time (UTC)  : %s" % fsutil.utc_iso(when),
        "Host        : %s" % socket.gethostname(),
        "",
    ]
    for r in ctx.results.values():
        lines.append(_line(r))
        if r.todo and r.status in (FAIL, WARN):
            lines.append("    What to do: %s" % r.todo)
    lines += [
        "",
        "Result      : %s%s" % ("READY" if ready else "NOT READY",
                                " (partial check)" if partial else ""),
        "Readiness code: %s" % code,
        "Tokens used : %d input + %d output = %d (%s)" % (
            ctx.tokens_in, ctx.tokens_out, ctx.tokens_in + ctx.tokens_out,
            "check 10" if sub else "checks 7-10"),
    ]
    return "\n".join(lines) + "\n"


def run(cfg, quick=False, as_json=False, only=None, route=None):
    cfg.require_all()
    route = route_mod.resolve(route)
    all_ids = list(names_for(route))
    if only:
        selected = parse_only(only)
    elif quick:
        selected = [1, 2, 3, 4, 5]
    else:
        selected = all_ids
    partial = selected != all_ids
    sub = route == route_mod.SUBSCRIPTION
    if not as_json:
        ui.out("DFIR Lab readiness check (lab version %s)%s" % (
            cfg.lab_version, " - quick" if quick else ""))
        if sub:
            ui.out("AI access: your own Claude subscription. Checks 6-9 test the Duke gateway "
                   "and do not apply (N/A).")
        ui.out("")

    def show(r):
        if as_json:
            return
        ui.out(_line(r))
        if r.todo and r.status in (FAIL, WARN):
            ui.out("    What to do: %s" % r.todo)

    ctx = run_checks(cfg, selected, on_result=show, route=route)
    r1 = ctx.results.get(1)
    if r1 is not None and r1.data.get("clock_amended") and not as_json:
        ui.out("Update to check 1 after talking to the gateway:")
        show(r1)
    when = fsutil.utc_now()
    date = when.strftime("%Y-%m-%d")
    pairs = [(r.id, r.status) for r in ctx.results.values()]
    code = readiness.compute(cfg.lab_version, pairs, date, route)
    ready = readiness.is_ready(pairs, route)
    any_fail = any(s == FAIL for _, s in pairs)
    doc = {
        "version": cfg.lab_version,
        "date": date,
        "time": fsutil.utc_iso(when),
        "case": cfg.get("case", "id"),
        "route": route,
        "partial": partial,
        "results": [{"id": r.id, "name": r.name, "status": r.status, "result": r.result,
                     "todo": r.todo} for r in ctx.results.values()],
        "ready": ready,
        "code": code,
        "tokens": {"input": ctx.tokens_in, "output": ctx.tokens_out,
                   "total": ctx.tokens_in + ctx.tokens_out},
    }
    report = build_report(cfg, ctx, code, ready, partial, when)
    stamp = fsutil.utc_stamp(when)
    rdir = paths.readiness_dir()
    try:
        txt = fsutil.write_text_atomic(rdir / ("readiness-%s.txt" % stamp), report)
        fsutil.write_text_atomic(rdir / "latest.txt", report)
        fsutil.write_json_atomic(rdir / ("readiness-%s.json" % stamp), doc)
        fsutil.write_json_atomic(rdir / "latest.json", doc)
    except OSError as exc:
        txt = None
        ui.problem("Could not save the readiness report (%s)." % (exc.strerror or exc),
                   "Check that your home folder is not full.")
    fsutil.log("check", code=code, ready=ready, partial=partial, route=route,
               results=",".join("%d=%s" % p for p in pairs))
    if as_json:
        ui.out(json.dumps(doc, indent=2))
    else:
        ui.out("")
        if ready:
            ui.out("READY - every check %spassed%s." % (
                "that applies to the subscription route " if sub else "",
                " (with warnings above)" if any(s == WARN for _, s in pairs) else ""))
        elif partial:
            ui.out("NOT READY - this was a partial check (%s). Run  dfir-lab check  without "
                   "options for a readiness code that counts." % (
                       "quick" if quick else "only " + ",".join(map(str, selected))))
        else:
            ui.out("NOT READY - fix the FAIL lines above (start with the first one), then run "
                   "dfir-lab check again.")
        ui.out("Readiness code: %s" % code)
        ui.out("Tokens used: %d (input %d, output %d)" % (
            ctx.tokens_in + ctx.tokens_out, ctx.tokens_in, ctx.tokens_out))
        if txt:
            ui.out("Report saved to %s" % txt)
    return 1 if any_fail else 0
