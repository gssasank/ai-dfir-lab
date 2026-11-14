#!/usr/bin/env python3
"""Build dist/bootstrap.sh: one self-contained setup script for the lab VM.

    python3 bootstrap/build.py                build dist/bootstrap.sh (+ .sha256)
    python3 bootstrap/build.py --check        build twice and confirm identical output
    python3 bootstrap/build.py standin-kit    build the stand-in case kit zip for tests

The script is bootstrap/bootstrap.sh.in with the release settings from
bootstrap/release.conf filled in and a compressed payload appended: the lab
toolkit (lab/ without its tests and instructor tools), the lab.conf template,
and the case overlay bootstrap/case-overlay/<CASE_ID>/. One file means the one
SHA-256 in the student command covers all of our code.

The build is deterministic: fixed file order, timestamps, owners and modes;
text files are normalised to LF line endings so a Windows checkout gives the
same bytes. Building twice gives the same SHA-256 (`--check` proves it).

Python 3.8+ standard library only. Run it anywhere (WSL, Linux, macOS,
Windows); the result depends only on the repository contents and zlib.
"""

import argparse
import base64
import gzip
import hashlib
import io
import os
import re
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
LAB = REPO / "lab"
DIST = REPO / "dist"
STANDIN_OUT = REPO / "build" / "bootstrap"

PLACEHOLDER_OWNER = "OWNER-NOT-SET"
FIXED_MTIME = 1767225600            # 2026-01-01T00:00:00Z, for every archive member
ZIP_DATE = (2026, 1, 1, 0, 0, 0)
MARKER = "__DFIR_LAB_PAYLOAD_BELOW__"

REQUIRED_KEYS = ("REPO_OWNER", "REPO_NAME", "VERSION", "CLAUDE_CODE_VERSION",
                 "PROTOCOL_SIFT_REPO", "PROTOCOL_SIFT_COMMIT", "GATEWAY_BASE_URL",
                 "MODEL_MAIN", "MODEL_SMALL", "CASE_ID", "CASE_TITLE", "CASE_KIT_PATTERN",
                 "CASE_KIT_SHA256")
VALUE_RE = re.compile(r"^[A-Za-z0-9._:/@+* -]*$")

# Toolkit files the VM needs. Tests and the instructor tools stay behind.
LAB_FILES = ["install.sh", "bin/dfir-lab", "bin/dfir-lab-configure-claude",
             "etc/lab.conf.example", "libexec/key-helper", "libexec/pretool-guard"]
LAB_TREES = ["lib/dfir_lab", "share"]
EXECUTABLE = {"lab/install.sh", "lab/bin/dfir-lab", "lab/bin/dfir-lab-configure-claude",
              "lab/libexec/key-helper", "lab/libexec/pretool-guard",
              "lab/share/readiness-sample/readiness.sh"}

KIT_REQUIRED = ("evidence/", "derived/", "README.md", "MANIFEST.sha256", "tools.list")


class BuildError(Exception):
    pass


# ---------------------------------------------------------------------------
# release.conf
# ---------------------------------------------------------------------------

def read_release(path):
    values = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^([A-Z][A-Z0-9_]*)="([^"]*)"$', line)
        if not m:
            raise BuildError('%s:%d: expected NAME="value", got %r' % (path, n, raw))
        name, value = m.groups()
        if not VALUE_RE.match(value):
            raise BuildError("%s:%d: %s has a character that is not allowed: %r"
                             % (path, n, name, value))
        values[name] = value
    missing = [k for k in REQUIRED_KEYS if not values.get(k)]
    if missing:
        raise BuildError("%s: missing or empty: %s" % (path, ", ".join(missing)))
    checks = [
        ("VERSION", r"^[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?$"),
        ("CLAUDE_CODE_VERSION", r"^[0-9]+\.[0-9]+\.[0-9]+$"),
        ("PROTOCOL_SIFT_COMMIT", r"^[0-9a-f]{40}$"),
        ("PROTOCOL_SIFT_REPO", r"^https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\.git$"),
        ("GATEWAY_BASE_URL", r"^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9._/-]*)?$"),
        ("CASE_ID", r"^[a-z0-9][a-z0-9_-]{0,40}$"),
        ("CASE_KIT_PATTERN", r"^[A-Za-z0-9._*-]+\.zip$"),
        ("REPO_NAME", r"^[A-Za-z0-9._-]+$"),
        ("REPO_OWNER", r"^[A-Za-z0-9-]+$"),
        ("MODEL_MAIN", r"^[A-Za-z0-9._:/@-]+$"),
        ("MODEL_SMALL", r"^[A-Za-z0-9._:/@-]+$"),
    ]
    for key, rx in checks:
        if not re.match(rx, values[key]):
            raise BuildError("release.conf: %s=%r does not look right" % (key, values[key]))
    if values["GATEWAY_BASE_URL"].rstrip("/").endswith("/v1"):
        raise BuildError("release.conf: GATEWAY_BASE_URL is the root URL, without /v1")
    hashes = values["CASE_KIT_SHA256"].split()
    bad = [h for h in hashes if not re.match(r"^[0-9a-f]{64}$", h)]
    if bad or not hashes:
        raise BuildError("release.conf: CASE_KIT_SHA256 must be one or more SHA-256 values "
                         "(64 lowercase hex), got %r. Build the stand-in kit with "
                         "`build.py standin-kit` and paste its hash, or paste the real kit's."
                         % values["CASE_KIT_SHA256"])
    vparts = tuple(int(x) for x in values["CLAUDE_CODE_VERSION"].split("."))
    if vparts < (2, 1, 295):
        raise BuildError("release.conf: the toolkit needs Claude Code 2.1.295 or later")
    return values


# ---------------------------------------------------------------------------
# payload
# ---------------------------------------------------------------------------

def _normalise(data):
    """LF line endings for text files (no NUL byte), so a checkout with CRLF
    builds the same bytes."""
    if b"\0" in data:
        return data
    return data.replace(b"\r\n", b"\n")


def _walk(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d != "__pycache__")
        for f in sorted(fns):
            if f.endswith((".pyc", ".pyo")):
                continue
            out.append(Path(dp) / f)
    return out


def payload_members(rel):
    """[(archive path, bytes)] sorted by path."""
    members = {}
    for f in LAB_FILES:
        members["lab/" + f] = (LAB / f).read_bytes()
    for t in LAB_TREES:
        for p in _walk(LAB / t):
            members["lab/" + p.relative_to(LAB).as_posix()] = p.read_bytes()

    tpl = (HERE / "lab.conf.in").read_text(encoding="utf-8")
    for key in ("VERSION", "GATEWAY_BASE_URL", "MODEL_MAIN", "MODEL_SMALL", "CASE_ID",
                "CASE_TITLE"):
        tpl = tpl.replace("@%s@" % key, rel[key])
    left = sorted(set(re.findall(r"@[A-Z_]+@", tpl)) - {"@CLAUDE_BINARY@"})
    if left:
        raise BuildError("lab.conf.in has unknown placeholders: %s" % ", ".join(left))
    members["lab.conf.in"] = tpl.encode("utf-8")

    overlay = HERE / "case-overlay" / rel["CASE_ID"]
    if not overlay.is_dir():
        raise BuildError("missing case overlay directory %s" % overlay)
    sums = []
    for p in _walk(overlay):
        relp = p.relative_to(overlay).as_posix()
        if not re.match(r"^[A-Za-z0-9._/-]+$", relp) or relp.startswith(".dfir-lab/") \
                or relp in ("MANIFEST.lab.sha256", "analysis", "reports", "exports"):
            raise BuildError("overlay file name not allowed: %s" % relp)
        if relp.split("/")[0] in ("analysis", "reports", "exports", ".claude"):
            raise BuildError("overlay may not write into a student folder: %s" % relp)
        data = _normalise(p.read_bytes())
        members["case-overlay/" + relp] = data
        sums.append("%s  %s\n" % (hashlib.sha256(data).hexdigest(), relp))
    if not sums:
        raise BuildError("the case overlay %s is empty" % overlay)
    members["overlay.sha256"] = "".join(sums).encode("utf-8")
    members["PAYLOAD-INFO"] = ("version=%s\ncase=%s\n" % (rel["VERSION"], rel["CASE_ID"])
                               ).encode("utf-8")
    return sorted((k, _normalise(v)) for k, v in members.items())


def build_tar_gz(members):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        dirs = set()
        for path, _ in members:
            parts = path.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))
        entries = [(d + "/", None) for d in dirs] + list(members)
        for path, data in sorted(entries, key=lambda e: e[0]):
            info = tarfile.TarInfo("payload/" + path.rstrip("/"))
            info.mtime = FIXED_MTIME
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if data is None:
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            else:
                info.size = len(data)
                info.mode = 0o755 if path in EXECUTABLE else 0o644
                tar.addfile(info, io.BytesIO(data))
    gz = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=gz, compresslevel=9,
                       mtime=0) as fh:
        fh.write(raw.getvalue())
    return gz.getvalue()


def render_script(rel, payload_gz):
    tpl = (HERE / "bootstrap.sh.in").read_text(encoding="utf-8").replace("\r\n", "\n")
    settings = [
        "R_VERSION='%s'" % rel["VERSION"],
        "R_CLAUDE_CODE_VERSION='%s'" % rel["CLAUDE_CODE_VERSION"],
        "R_PROTOCOL_SIFT_REPO='%s'" % rel["PROTOCOL_SIFT_REPO"],
        "R_PROTOCOL_SIFT_COMMIT='%s'" % rel["PROTOCOL_SIFT_COMMIT"],
        "R_GATEWAY_BASE_URL='%s'" % rel["GATEWAY_BASE_URL"],
        "R_CASE_ID='%s'" % rel["CASE_ID"],
        "R_CASE_TITLE='%s'" % rel["CASE_TITLE"],
        "R_CASE_KIT_PATTERN='%s'" % rel["CASE_KIT_PATTERN"],
        "R_CASE_KIT_SHA256='%s'" % " ".join(rel["CASE_KIT_SHA256"].split()),
        "R_PAYLOAD_SHA256='%s'" % hashlib.sha256(payload_gz).hexdigest(),
    ]
    if tpl.count("@@RELEASE_SETTINGS@@") != 1:
        raise BuildError("bootstrap.sh.in must contain @@RELEASE_SETTINGS@@ exactly once")
    if MARKER in tpl:
        raise BuildError("bootstrap.sh.in must not contain the payload marker")
    script = tpl.replace("@@RELEASE_SETTINGS@@", "\n".join(settings))
    if not script.endswith("\n"):
        script += "\n"
    b64 = base64.b64encode(payload_gz).decode("ascii")
    lines = [b64[i:i + 76] for i in range(0, len(b64), 76)]
    return (script + MARKER + "\n" + "\n".join(lines) + "\n").encode("ascii")


def build(out_dir):
    rel = read_release(HERE / "release.conf")
    payload = build_tar_gz(payload_members(rel))
    data = render_script(rel, payload)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "bootstrap.sh"
    out.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (out_dir / "bootstrap.sh.sha256").write_text("%s  bootstrap.sh\n" % digest,
                                                 encoding="ascii")
    return rel, out, digest, len(payload)


def student_command(rel, digest, base_url=None):
    if base_url is None:
        base_url = "https://raw.githubusercontent.com/%s/%s/v%s" % (
            rel["REPO_OWNER"], rel["REPO_NAME"], rel["VERSION"])
    return ('curl -fsSLO %s/dist/bootstrap.sh && echo "%s  bootstrap.sh" | sha256sum -c '
            '&& bash bootstrap.sh' % (base_url.rstrip("/"), digest))


# ---------------------------------------------------------------------------
# stand-in case kit
# ---------------------------------------------------------------------------

STANDIN_README = """# Stand-in case kit (TEST ONLY)

This zip is a stand-in for the real case kit, built by
`python3 bootstrap/build.py standin-kit` from the toolkit's synthetic sample
case. It has the layout the real kit will have:

    <top-level directory>/
      evidence/         the evidence (read-only once installed)
      derived/          pre-processed copies of the evidence
      README.md         this file
      MANIFEST.sha256   SHA-256 of every file under evidence/ and derived/
      tools.list        tools the lab needs: "command<TAB>version-args" per line

Nothing in it is a real incident.
"""


def standin_kit(out_path):
    """A deterministic stand-in kit zip shaped like the real one."""
    src = LAB / "tests" / "fixtures" / "sample-case"
    top = out_path.stem
    files = {}
    for p in sorted((src / "evidence").iterdir()):
        files["evidence/" + p.name] = _normalise(p.read_bytes())
    # derived/: a pre-processed copy, as the real kit will carry.
    sec = files["evidence/security-events.csv"].decode("utf-8").splitlines()
    logons = [l for l in sec[1:] if l.split(",")[2] in ("4624", "4625")]
    files["derived/logons.csv"] = ("\n".join([sec[0]] + logons) + "\n").encode("utf-8")
    manifest = "".join("%s  %s\n" % (hashlib.sha256(d).hexdigest(), n)
                       for n, d in sorted(files.items()))
    files["README.md"] = STANDIN_README.encode("utf-8")
    files["MANIFEST.sha256"] = manifest.encode("utf-8")
    files["tools.list"] = _normalise((src / "tools.list").read_bytes())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        names = sorted(set([top + "/", top + "/evidence/", top + "/derived/"] +
                           ["%s/%s" % (top, n) for n in files]))
        for name in names:
            zi = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            zi.create_system = 3                      # Unix
            if name.endswith("/"):
                zi.external_attr = (0o40755 << 16) | 0x10
                z.writestr(zi, b"")
            else:
                zi.external_attr = 0o100644 << 16
                z.writestr(zi, files[name[len(top) + 1:]])
    out_path.write_bytes(buf.getvalue())
    return hashlib.sha256(buf.getvalue()).hexdigest()


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", nargs="?", default="bootstrap", choices=("bootstrap", "standin-kit"))
    ap.add_argument("--check", action="store_true",
                    help="build twice into temporary folders and compare the SHA-256")
    ap.add_argument("--out", help="output folder (default: dist/, or build/bootstrap/ for "
                                  "the stand-in kit)")
    ap.add_argument("--base-url", help="print the student command for this base URL "
                                       "instead of raw.githubusercontent.com (testing)")
    args = ap.parse_args(argv)
    try:
        if args.what == "standin-kit":
            out_dir = Path(args.out) if args.out else STANDIN_OUT
            out = out_dir / "szechuan-kit-standin.zip"
            digest = standin_kit(out)
            again = standin_kit(Path(tempfile.mkdtemp()) / out.name)
            if again != digest:
                raise BuildError("stand-in kit build is not deterministic")
            print("Stand-in kit: %s" % out)
            print("SHA-256:      %s" % digest)
            print("Accepted by release.conf: %s" % (
                "yes" if digest in read_release_loose().split() else
                "NO - put this hash in CASE_KIT_SHA256"))
            return 0
        if args.check:
            a = build(Path(tempfile.mkdtemp(prefix="dfir-build-a-")))[2]
            b = build(Path(tempfile.mkdtemp(prefix="dfir-build-b-")))[2]
            print("build 1: %s\nbuild 2: %s" % (a, b))
            if a != b:
                raise BuildError("the two builds differ")
            print("Deterministic: yes")
        rel, out, digest, psize = build(Path(args.out) if args.out else DIST)
        print("Built %s (%d bytes, payload %d bytes compressed)" % (out, out.stat().st_size,
                                                                     psize))
        print("SHA-256: %s" % digest)
        print("Version %s, Claude Code %s, Protocol SIFT %s, case %s, %d accepted kit hash(es)"
              % (rel["VERSION"], rel["CLAUDE_CODE_VERSION"], rel["PROTOCOL_SIFT_COMMIT"][:12],
                 rel["CASE_ID"], len(rel["CASE_KIT_SHA256"].split())))
        if args.base_url:
            print("\nTest command (NOT for students):\n%s" % student_command(rel, digest,
                                                                           args.base_url))
        elif rel["REPO_OWNER"] == PLACEHOLDER_OWNER:
            print("\nNOT FOR RELEASE: REPO_OWNER in bootstrap/release.conf is still the "
                  "placeholder %r,\nso no student command is printed. Set it, rebuild, commit "
                  "dist/bootstrap.sh and tag v%s." % (PLACEHOLDER_OWNER, rel["VERSION"]))
        else:
            print("\nStudent command (commit dist/bootstrap.sh and push tag v%s first):\n%s"
                  % (rel["VERSION"], student_command(rel, digest)))
        return 0
    except (BuildError, OSError) as exc:
        print("build.py: %s" % exc, file=sys.stderr)
        return 1


def read_release_loose():
    try:
        text = (HERE / "release.conf").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'^CASE_KIT_SHA256="([^"]*)"', text, re.M)
    return m.group(1) if m else ""


if __name__ == "__main__":
    sys.exit(main())
