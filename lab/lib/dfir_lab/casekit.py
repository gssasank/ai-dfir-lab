"""Case kit helpers: manifest, evidence protection, output directories and
the worksheet."""

import hashlib
import os
import re
import shutil
from pathlib import Path

from .errors import LabError

OUTPUT_DIRS = ("analysis", "reports", "exports")


def parse_manifest(path):
    """Lines in sha256sum format: '<64 hex>  <relative path>' (or ' *path').
    Returns a list of (hexdigest, relpath)."""
    entries = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
            if not m:
                raise LabError("The case manifest %s has a line that is not '<sha256>  <file>': %r"
                               % (path, line[:80]), "Tell the TA; the case kit is damaged.")
            entries.append((m.group(1).lower(), m.group(2).strip()))
    return entries


def sha256_file(path, bufsize=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def evidence_dirs(case_dir, manifest_entries):
    """Directories that hold evidence (parents of manifest files), excluding
    the case directory itself."""
    case_dir = Path(case_dir).resolve()
    out = set()
    for _, rel in manifest_entries:
        p = (case_dir / rel).resolve().parent
        while p != case_dir and case_dir in p.parents:
            out.add(p)
            p = p.parent
    return out


def is_evidence_path(path, case_dir, manifest_entries):
    p = Path(path).resolve()
    case_dir = Path(case_dir).resolve()
    files = {(case_dir / rel).resolve() for _, rel in manifest_entries}
    if p in files:
        return True
    for d in evidence_dirs(case_dir, manifest_entries):
        if p == d or d in p.parents:
            return True
    return False


def load_manifest_quiet(cfg):
    m = cfg.case_path("manifest")
    if m and m.is_file():
        try:
            return parse_manifest(m)
        except (LabError, OSError):
            return []
    return []


def ensure_output_dirs(cfg):
    """Create analysis/, reports/, exports/ under the case directory. Never
    inside evidence."""
    case_dir = cfg.case_dir
    if not case_dir.is_dir():
        raise LabError("The case folder %s does not exist." % case_dir,
                       "The case kit is not installed. Tell the TA.", code=3)
    entries = load_manifest_quiet(cfg)
    made = []
    for name in OUTPUT_DIRS:
        d = case_dir / name
        if is_evidence_path(d, case_dir, entries):
            raise LabError("Refusing to create %s because it is inside the evidence." % d,
                           "Tell the TA; the case kit layout needs fixing.", code=3)
        if not d.is_dir():
            try:
                d.mkdir(parents=False)
            except OSError as exc:
                raise LabError("Could not create %s (%s)." % (d, exc.strerror or exc),
                               "Tell the TA; the case folder should be writable by you.", code=3)
            made.append(d)
    return made


def ensure_worksheet(cfg):
    """Create the student's worksheet from the template if it is missing.
    Does nothing when [case] worksheet or worksheet_template is unset (the
    default: students fill in the worksheet outside the VM)."""
    ws = cfg.case_path("worksheet")
    tpl = cfg.case_path("worksheet_template")
    if ws and not ws.exists() and tpl and tpl.is_file():
        entries = load_manifest_quiet(cfg)
        if is_evidence_path(ws, cfg.case_dir, entries):
            return ws
        shutil.copyfile(tpl, ws)
        os.chmod(ws, 0o644)
    return ws

