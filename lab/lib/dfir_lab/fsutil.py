"""Small file helpers: atomic writes (always redacted), log lines, timestamps."""

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path

from . import paths
from .redact import redact


def utc_now():
    return _dt.datetime.now(_dt.timezone.utc)


def utc_stamp(dt=None):
    """Compact UTC timestamp for file names, e.g. 20261116T140501Z."""
    return (dt or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def utc_iso(dt=None):
    return (dt or utc_now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_dir(path, mode=0o755):
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True, mode=mode)
    return p


def write_text_atomic(path, text, mode=0o644, redacted=True):
    """Write text to path atomically. Text is redacted unless told otherwise
    (only the key store writes unredacted)."""
    path = Path(path)
    ensure_dir(path.parent)
    data = redact(text) if redacted else text
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_json_atomic(path, obj, mode=0o644):
    return write_text_atomic(path, json.dumps(obj, indent=2, sort_keys=False) + "\n", mode=mode)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def append_line(path, line):
    path = Path(path)
    try:
        ensure_dir(path.parent)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(redact(line).replace("\n", " ") + "\n")
    except OSError:
        pass


def log(event, **fields):
    """One redacted line in ~/dfir-lab/logs/dfir-lab.log. Never raises."""
    parts = [utc_iso(), event]
    for k, v in fields.items():
        parts.append("%s=%s" % (k, json.dumps(v) if not isinstance(v, (int, float)) else v))
    append_line(paths.toolkit_log(), " ".join(parts))


def tail(path, n=50):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    return [redact(l.rstrip("\n")) for l in lines[-n:]]
