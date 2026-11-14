"""Storage of the student's personal gateway key.

The key lives in ~/.config/dfir-lab/duke-api-key (directory 0700, file 0600).
This is the only module that writes the key unredacted.
"""

import os
import stat
import tempfile

from . import paths, redact
from .errors import LabError

MIN_LEN = 8
_QUOTES = "\"'`‘’“”"


def normalize(raw):
    """Strip surrounding whitespace and quotes (people often copy them)."""
    value = (raw or "").strip()
    while len(value) >= 2 and value[0] in _QUOTES and value[-1] in _QUOTES:
        value = value[1:-1].strip()
    return value


def problems(key):
    """Return a list of plain-language reasons the key is unacceptable."""
    if not key:
        return ["The key is empty. Nothing was pasted."]
    out = []
    if any(c.isspace() for c in key):
        out.append("The key contains spaces or line breaks. A key is one unbroken "
                   "string; you may have copied extra text with it.")
    if len(key) < MIN_LEN:
        out.append("The key is only %d characters long; a real key is longer. You may "
                   "have copied only part of it." % len(key))
    return out


def validate(key):
    found = problems(key)
    if found:
        raise LabError(" ".join(found),
                       "Copy the whole key again from the Duke AI dashboard and paste it.")
    return key


def _fsync_dir(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def save(key):
    """Atomically store a validated key. Returns the path."""
    validate(key)
    d = paths.key_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".duke-api-key.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(key + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, paths.key_file())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(d)
    redact.reset(key)
    return paths.key_file()


def load():
    """The stored key, or None when absent or unreadable."""
    try:
        with open(paths.key_file(), encoding="utf-8") as fh:
            value = fh.read().strip()
    except OSError:
        return None
    if value:
        redact.add_secret(value)
    return value or None


def clear():
    """Delete the stored key. Returns True when a key was deleted."""
    p = paths.key_file()
    try:
        p.unlink()
        removed = True
    except FileNotFoundError:
        removed = False
    redact.reset()
    return removed


def status():
    p = paths.key_file()
    info = {"present": False, "path": str(p)}
    try:
        st = p.stat()
    except OSError:
        return info
    key = load()
    info.update(
        present=bool(key),
        mode=stat.S_IMODE(st.st_mode),
        owner_ok=(st.st_uid == os.getuid()),
        length=len(key) if key else 0,
        last4=key[-4:] if key and len(key) >= MIN_LEN else "",
        problems=problems(key) if key else ["The key file is empty."],
    )
    return info
