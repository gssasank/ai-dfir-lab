"""The one redaction function.

Every piece of text the toolkit writes to the screen or to a file goes
through `redact()`, which replaces the stored key with MARK.

Scope, stated honestly: it catches the key in the listed forms below, not
under arbitrary transformations. Anything else (encryption, compression,
reversing, hex dumps, UTF-16 or other byte encodings, double encoding, a key
split across separate writes) is NOT recognised. Callers therefore redact a
whole record or body before they split, wrap, page, or truncate it.

Recognised forms:
- the key itself;
- whitespace inserted between any two characters: any Unicode whitespace
  (for example a wrapped line, a tab, U+2003 EM SPACE), the zero-width
  characters U+200B-U+200D, U+2060 and U+FEFF, and the literal escape
  sequences \\n \\r \\t (a JSON-escaped line break);
- each character, independently of the others, written as itself, as a %XX
  URL escape with upper- or lower-case hex (so both normal URL encoding and
  every-character percent encoding), as a JSON \\uXXXX escape in either case,
  or, for quotes, slash and backslash, with a backslash in front (JSON and
  Python string escaping);
- standard or URL-safe base64 of the key, on its own or inside a longer
  base64 string at any alignment (the characters fully determined by the
  key), also when the base64 text is wrapped across lines.

Cost: one regular expression compiled per stored key; matching is linear in
the length of the text for practical key lengths (see test_redact.py for a
time bound).
"""

import base64
import re

from . import paths

MARK = "[REDACTED-KEY]"
MIN_SECRET_LEN = 4
# Between two encoded key characters we tolerate whitespace (Unicode-aware),
# zero-width characters, and literal \n \r \t escape sequences.
_SEP = r"(?:[\s​‌‍⁠﻿]|\\[nrt])*"
_B64_URLSAFE = str.maketrans("+/", "-_")


def _char_pattern(c):
    """One key character in any of the recognised spellings."""
    alts = [re.escape(c)]
    alts.append("".join("%%(?i:%02x)" % b for b in c.encode("utf-8")))
    cp = ord(c)
    if cp < 0x10000:
        units = [cp]
    else:
        cp -= 0x10000
        units = [0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)]
    alts.append("".join(r"\\u(?i:%04x)" % u for u in units))
    if c in "\"'\\/":
        alts.append(re.escape("\\" + c))
    return "(?:%s)" % "|".join(alts)


def _base64_cores(secret):
    """The parts of base64(secret) that do not depend on surrounding bytes,
    for each of the three possible alignments inside a longer string."""
    raw = secret.encode("utf-8")
    cores = set()
    for off in range(3):
        enc = base64.b64encode(b"\0" * off + raw).decode("ascii")
        start = (off * 8 + 5) // 6              # first char free of the prefix
        end = ((off + len(raw)) * 8) // 6       # chars fully covered by key bits
        core = enc[start:end]
        if len(core) >= 8:
            cores.add(core)
            cores.add(core.translate(_B64_URLSAFE))
    return cores


def _secret_patterns(secret):
    pats = [_SEP.join(_char_pattern(c) for c in secret)]
    for core in sorted(_base64_cores(secret), key=len, reverse=True):
        pats.append(_SEP.join(re.escape(c) for c in core))
    return pats


class Redactor:
    def __init__(self, secrets=()):
        self._secrets = []
        self._re = None
        for s in secrets:
            self.add(s)

    def add(self, secret):
        if not secret or len(secret) < MIN_SECRET_LEN or secret in self._secrets:
            return
        self._secrets.append(secret)
        self._compile()

    @property
    def active(self):
        return bool(self._secrets)

    def _compile(self):
        parts = []
        for s in self._secrets:
            parts.extend(_secret_patterns(s))
        # Longest alternatives first so a full match wins over a partial one.
        parts.sort(key=len, reverse=True)
        self._re = re.compile("|".join("(?:%s)" % p for p in parts))

    def redact(self, text):
        return self.redact_count(text)[0]

    def redact_count(self, text):
        if text is None:
            return "", 0
        if not isinstance(text, str):
            text = str(text)
        if not self._re:
            return text, 0
        return self._re.subn(MARK, text)

    def redact_bytes(self, data):
        """Bytes are decoded as UTF-8 (undecodable bytes kept as they are), so
        Unicode whitespace between key characters is recognised too."""
        if not self._re:
            return data, 0
        text, n = self._re.subn(MARK, data.decode("utf-8", errors="surrogateescape"))
        return text.encode("utf-8", errors="surrogateescape"), n


_current = None


def _read_stored_key():
    try:
        with open(paths.key_file(), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def get():
    global _current
    if _current is None:
        _current = Redactor([_read_stored_key()])
    return _current


def reset(extra=None):
    """Reload the stored key (call after the key changes)."""
    global _current
    _current = Redactor([_read_stored_key()])
    if extra:
        _current.add(extra)
    return _current


def add_secret(secret):
    get().add(secret)


def redact(text):
    return get().redact(text)


def redact_count(text):
    return get().redact_count(text)


def redact_bytes(data):
    return get().redact_bytes(data)
