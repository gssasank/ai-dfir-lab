"""dfir-lab mount / unmount / mount --status: the case's disk image.

The image (E01 segments under /cases/<case>/disk/) is mounted read-only and
noexec by a small root helper (libexec/disk-mount) run through sudo:
ewfmount -> read-only loop device over the Windows partition -> ntfs-3g with
ro,noexec,nodev,nosuid. Nothing is mounted after a reboot; `dfir-lab mount`
and `dfir-lab start` put it back.

Reading the state never needs sudo: it comes from /proc/self/mountinfo and
/sys/block/loopN. Whether the mount is safe is decided from the mount table
(options and the loop device's read-only flag), never by trying to write to
it or run anything from it. The disk image holds live malware.

Settings ([disk] in lab.conf): image, segments ("name:sha256 ..."),
mount_point, ewf_mount, probe, zip_name. An empty `image` means the case has
no disk image; the commands then say so and checks skip it.
"""

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import casekit, fsutil, paths, ui
from .errors import ConfigError, LabError

SAFE_OPTIONS = ("ro", "noexec", "nodev", "nosuid")
HELPER_NAME = "disk-mount"
HELPER_TIMEOUT_S = 180
PROBE_BYTES = 4


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

@dataclass
class DiskSettings:
    image: Path
    segments: list            # [(file name, sha256)]
    mount_point: Path
    ewf_mount: Path
    probe: str
    probe_magic: bytes
    zip_name: str

    @property
    def folder(self):
        return self.image.parent

    @property
    def raw(self):
        return self.ewf_mount / "ewf1"


def settings(cfg):
    """DiskSettings, or None when the case has no disk image."""
    image = cfg.get("disk", "image")
    if not image:
        return None
    img = Path(image)
    if not img.is_absolute():
        img = cfg.case_dir / img
    segs = []
    for item in cfg.get("disk", "segments").split():
        name, sep, digest = item.partition(":")
        if not sep or len(digest) != 64 or "/" in name:
            raise ConfigError("The setting [disk] segments in %s has an entry that is not "
                              "name:sha256: %r." % (cfg.path, item),
                              "Tell the TA; the settings file needs to be fixed.")
        segs.append((name, digest.lower()))
    mnt = Path(cfg.get("disk", "mount_point") or "/mnt/dc01")
    ewf = cfg.get("disk", "ewf_mount")
    ewf = Path(ewf) if ewf else mnt.parent / ("ewf_" + mnt.name)
    for p in (mnt, ewf):
        if not p.is_absolute() or any(c.isspace() for c in str(p)):
            raise ConfigError("The disk mount folder %r in %s must be an absolute path without "
                              "spaces." % (str(p), cfg.path),
                              "Tell the TA; the settings file needs to be fixed.")
    return DiskSettings(image=img, segments=segs, mount_point=mnt, ewf_mount=ewf,
                        probe=cfg.get("disk", "probe") or "Windows/System32/config/SYSTEM",
                        probe_magic=(cfg.get("disk", "probe_magic") or "regf").encode("ascii"),
                        zip_name=cfg.get("disk", "zip_name") or "the disk image zip")


def require_settings(cfg):
    ds = settings(cfg)
    if ds is None:
        raise LabError("This lab has no disk image configured, so there is nothing to mount.",
                       "Nothing to do. (The setting [disk] image in lab.conf is empty.)")
    return ds


def missing_todo(ds):
    return ("Inside this VM, download the disk image (%s) from the course site into "
            "~/Downloads, then run the lab setup command again." % ds.zip_name)


# --------------------------------------------------------------------------
# Mount table (no sudo needed)
# --------------------------------------------------------------------------

def _unescape(s):
    """mountinfo escapes space, tab, newline and backslash as \\ooo."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 3 < len(s) + 1 and s[i + 1:i + 4].isdigit():
            out.append(chr(int(s[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def parse_mountinfo(text):
    """[{'mount_point', 'options' (set), 'fstype', 'source'}] in table order."""
    mounts = []
    for line in text.splitlines():
        parts = line.split()
        if "-" not in parts:
            continue
        sep = parts.index("-")
        if sep < 6 or len(parts) < sep + 3:
            continue
        mounts.append({"mount_point": _unescape(parts[4]),
                       "options": set(parts[5].split(",")),
                       "fstype": parts[sep + 1],
                       "source": _unescape(parts[sep + 2])})
    return mounts


def read_mountinfo(path="/proc/self/mountinfo"):
    try:
        with open(os.environ.get("DFIR_LAB_MOUNTINFO") or path, encoding="utf-8",
                  errors="replace") as fh:
            return parse_mountinfo(fh.read())
    except OSError:
        return []


def _sys_block():
    return Path(os.environ.get("DFIR_LAB_SYS_BLOCK") or "/sys/block")


def loop_info(source):
    """(backing file, read-only flag) of /dev/loopN, or (None, None)."""
    name = os.path.basename(source or "")
    if not (source or "").startswith("/dev/loop") or not name[4:].isdigit():
        return None, None
    base = _sys_block() / name
    try:
        backing = (base / "loop" / "backing_file").read_text().strip()
    except OSError:
        backing = None
    try:
        ro = (base / "ro").read_text().strip() == "1"
    except OSError:
        ro = None
    return backing, ro


@dataclass
class DiskState:
    state: str                       # "mounted" | "not-mounted" | "problem"
    problems: list = field(default_factory=list)
    segments_present: bool = False
    missing: list = field(default_factory=list)
    ewf_mounted: bool = False
    mounts: int = 0
    options: set = field(default_factory=set)
    fstype: str = ""
    source: str = ""
    foreign: bool = False            # mounted from something other than the image
    probe_ok: bool = False

    @property
    def mounted(self):
        return self.state == "mounted"


def segments_present(ds):
    missing = [n for n, _ in ds.segments if not (ds.folder / n).is_file()]
    if not ds.image.exists():
        missing.insert(0, ds.image.name)
    return missing


def probe_read(ds):
    try:
        with open(ds.mount_point / ds.probe, "rb") as fh:
            return fh.read(PROBE_BYTES) == ds.probe_magic[:PROBE_BYTES]
    except OSError:
        return False


def status(ds, mounts=None):
    mounts = read_mountinfo() if mounts is None else mounts
    st = DiskState(state="not-mounted")
    st.missing = segments_present(ds)
    st.segments_present = not st.missing
    st.ewf_mounted = any(m["mount_point"] == str(ds.ewf_mount) for m in mounts)
    here = [m for m in mounts if m["mount_point"] == str(ds.mount_point)]
    st.mounts = len(here)
    if not here:
        return st
    top = here[-1]
    st.options, st.fstype = top["options"], top["fstype"]
    if len(here) > 1:
        st.problems.append("%d file systems are stacked on %s" % (len(here), ds.mount_point))
    st.source = top["source"]
    backing, ro = loop_info(top["source"])
    if backing != str(ds.raw):
        st.foreign = True
        st.problems.append("%s is mounted from %s, not from the case's disk image"
                           % (ds.mount_point, top["source"]))
    elif ro is not True:
        st.problems.append("the loop device under %s is not read-only" % ds.mount_point)
    lacking = [o for o in SAFE_OPTIONS if o not in top["options"]]
    if lacking:
        st.problems.append("the mount table does not show %s for %s"
                           % (", ".join(lacking), ds.mount_point))
    if not st.problems:
        st.probe_ok = probe_read(ds)
        if not st.probe_ok:
            st.problems.append("%s cannot be read on %s" % (ds.probe, ds.mount_point))
    st.state = "problem" if st.problems else "mounted"
    return st


# --------------------------------------------------------------------------
# Mount and unmount through the root helper
# --------------------------------------------------------------------------

def _helper():
    return os.environ.get("DFIR_LAB_DISK_HELPER") or str(paths.libexec_dir() / HELPER_NAME)


def _sudo_prefix():
    """sudo for the helper. DFIR_LAB_SUDO overrides it (the tests set it to an
    empty value to run a fake helper directly)."""
    env = os.environ.get("DFIR_LAB_SUDO")
    if env is not None:
        return env.split()
    # In a terminal sudo may ask for a password (not on the SIFT VM, where
    # sudo needs none); without one it must not wait for input.
    return ["sudo"] if sys.stdin is not None and sys.stdin.isatty() else ["sudo", "-n"]


def run_helper(ds, action):
    """Returns (code, text) from the helper's RESULT line."""
    argv = _sudo_prefix() + [_helper(), action, str(ds.image), str(ds.ewf_mount),
                             str(ds.mount_point)]
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           timeout=HELPER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, "the mount helper did not finish within %d s" % HELPER_TIMEOUT_S
    except OSError as exc:
        return 127, "could not run the mount helper (%s)" % (exc.strerror or exc)
    out = p.stdout or ""
    fsutil.log("disk." + action, rc=p.returncode, out=out.strip()[-300:])
    for line in reversed(out.splitlines()):
        if line.startswith("RESULT "):
            parts = line.split(" ", 2)
            try:
                return int(parts[1]), (parts[2] if len(parts) > 2 else "")
            except ValueError:
                break
    if "password" in out.lower():
        return 126, "sudo asked for a password"
    return p.returncode or 31, out.strip()[-200:] or "no output"


def _mount_error(ds, code, text):
    mnt = ds.mount_point
    if code == 20:
        return LabError("The disk image is not complete on this VM (%s)." % text,
                        missing_todo(ds))
    if code == 21:
        return LabError("%s is already used by another mount (%s), so the disk image was not "
                        "mounted on top of it." % (mnt, text),
                        "Run  dfir-lab unmount  and then  dfir-lab mount . If that does not "
                        "help, restart the VM and run  dfir-lab mount  again.")
    if code == 22:
        return LabError("The folder %s is not empty, so nothing was mounted there." % text.split()[0],
                        "Tell the TA. Do not delete anything there yourself.")
    if code == 27:
        return LabError("The disk image was mounted, but the mount table did not show it as "
                        "read-only and noexec, so it was unmounted again. (%s)" % text,
                        "Tell the TA and send the output of  dfir-lab report-problem .")
    if code == 126:
        return LabError("Mounting needs administrator rights, and sudo asked for a password.",
                        "Run  dfir-lab mount  in a terminal and type your password "
                        "(forensics) when asked.")
    return LabError("The disk image could not be mounted (%s)." % text,
                    "Run  dfir-lab mount  again. If it fails again, restart the VM and try "
                    "once more, then run  dfir-lab report-problem  and send the file to the TA.")


def mount(ds, quiet=False):
    """Mount the image if needed. Returns the final DiskState; raises LabError."""
    st = status(ds)
    if st.missing:
        raise LabError("The disk image is not on this VM (missing: %s)."
                       % ", ".join(st.missing[:3]), missing_todo(ds))
    if st.mounted:
        return st
    if st.state == "problem" and st.foreign:
        raise _mount_error(ds, 21, "%s, %s" % (st.source, st.fstype))
    if st.state == "problem":
        raise LabError("Something is wrong with what is mounted at %s: %s."
                       % (ds.mount_point, "; ".join(st.problems)),
                       "Run  dfir-lab unmount  and then  dfir-lab mount .")
    if not quiet:
        ui.out("Mounting the disk image read-only at %s (takes a few seconds)..."
               % ds.mount_point)
    code, text = run_helper(ds, "mount")
    if code not in (0, 10):
        raise _mount_error(ds, code, text)
    st = status(ds)
    if not st.mounted:
        raise LabError("The disk image mount could not be confirmed from the mount table: %s."
                       % ("; ".join(st.problems) or "nothing is mounted at %s" % ds.mount_point),
                       "Run  dfir-lab unmount  and then  dfir-lab mount . If it happens again, "
                       "send the output of  dfir-lab report-problem  to the TA.")
    return st


def unmount(ds):
    st = status(ds)
    if st.mounts == 0 and not st.ewf_mounted:
        return False
    code, text = run_helper(ds, "unmount")
    if code == 30:
        raise LabError("%s is in use, so it could not be unmounted." % ds.mount_point,
                       "Close every terminal or program that is looking at a folder inside "
                       "%s (for example type  cd ~  in each terminal, and quit Claude Code), "
                       "then run  dfir-lab unmount  again." % ds.mount_point)
    if code == 31 and "something other" in text:
        raise LabError("%s holds a mount that is not the case's disk image, so it was left "
                       "alone." % ds.mount_point,
                       "Restart the VM; nothing is mounted after a restart.")
    if code not in (0, 11):
        raise LabError("The disk image could not be unmounted (%s)." % text,
                       "Restart the VM; nothing is mounted after a restart.")
    return True


# --------------------------------------------------------------------------
# Segment verification (used by readiness check 3)
# --------------------------------------------------------------------------

def _cache_file():
    return paths.state_dir() / "disk-verified.json"


def _stat_sig(p):
    s = os.stat(p)
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_uid, s.st_mode]


def verify_segments(ds):
    """Returns (bad, writable): segment names whose SHA-256 differs from the
    expected value, and those the student could change.

    A segment's hash is cached with its inode, size, mtime, ctime, owner and
    mode; the setup makes the segments root-owned and read-only, so any change
    to the file changes its ctime and forces a new hash."""
    cache = fsutil.read_json(_cache_file(), default={}) or {}
    if not isinstance(cache, dict):
        cache = {}
    new, bad, writable = {}, [], []
    for name, digest in ds.segments:
        p = ds.folder / name
        sig = _stat_sig(p)
        hit = cache.get(name)
        if isinstance(hit, dict) and hit.get("stat") == sig and hit.get("sha256") == digest:
            actual = digest
        else:
            actual = casekit.sha256_file(p, bufsize=4 * 1024 * 1024)
        if actual != digest:
            bad.append(name)
        else:
            new[name] = {"stat": sig, "sha256": actual}
        if os.access(p, os.W_OK):
            writable.append(name)
    try:
        fsutil.write_json_atomic(_cache_file(), new)
    except OSError:
        pass
    return bad, writable


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def describe(ds, st):
    lines = ["Disk image : %s (%s)" % (
        ds.image, "all %d files present" % len(ds.segments) if not st.missing
        else "MISSING: " + ", ".join(st.missing))]
    if st.mounted:
        lines.append("Mounted    : yes, read-only and noexec at %s (%s)" % (ds.mount_point,
                                                                         st.fstype))
    elif st.state == "problem":
        lines.append("Mounted    : PROBLEM: " + "; ".join(st.problems))
    else:
        lines.append("Mounted    : no")
    return lines


def run_mount(cfg, status_only=False):
    ds = require_settings(cfg)
    if status_only:
        st = status(ds)
        for line in describe(ds, st):
            ui.out(line)
        if st.missing:
            ui.out("What to do : " + missing_todo(ds))
        elif st.state == "problem":
            ui.out("What to do : run  dfir-lab unmount  and then  dfir-lab mount")
        elif not st.mounted:
            ui.out("What to do : run  dfir-lab mount")
        return 0 if st.mounted else 1
    before = status(ds)
    st = mount(ds)
    if before.mounted:
        ui.out("The disk image is already mounted read-only (noexec) at %s." % ds.mount_point)
    else:
        ui.out("The disk image is mounted read-only (noexec) at %s." % ds.mount_point)
    ui.out("Look around with:  ls %s" % ds.mount_point)
    ui.out("It holds live malware: read it, never run anything from it.")
    fsutil.log("mount", state=st.state)
    return 0


def run_unmount(cfg):
    ds = require_settings(cfg)
    if unmount(ds):
        ui.out("The disk image is unmounted. Mount it again with:  dfir-lab mount")
    else:
        ui.out("Nothing was mounted. (Mount the disk image with:  dfir-lab mount)")
    return 0
