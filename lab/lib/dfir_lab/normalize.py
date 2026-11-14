"""Answer normalisation rules for checkpoints (spec 5.4)."""

import datetime as _dt
import ipaddress
import re

RULES = ("exact", "casefold", "integer", "ip", "timestamp-utc-seconds", "regex-extract")


class FormatProblem(ValueError):
    """The answer is not in the expected form (not counted as a wrong attempt)."""


def _integer(s):
    t = s.strip().lstrip("#").replace(",", "").replace("_", "").replace(" ", "")
    try:
        if re.fullmatch(r"[+-]?0[xX][0-9a-fA-F]+", t):
            return str(int(t, 16))
        if re.fullmatch(r"[+-]?\d+", t):
            return str(int(t))
    except ValueError:
        pass
    raise FormatProblem("This answer should be a whole number, such as 4624.")


def _ip(s):
    t = s.strip().strip("[]")
    m = re.fullmatch(r"(\d{1,3}(?:\.\d{1,3}){3}):\d+", t)      # IPv4 with a port
    if m:
        t = m.group(1)
    m = re.fullmatch(r"\[([0-9a-fA-F:.]+)\]:\d+", s.strip())   # [IPv6]:port
    if m:
        t = m.group(1)
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", t):
        t = ".".join(str(int(o)) for o in t.split("."))       # drop leading zeros
    try:
        return str(ipaddress.ip_address(t))
    except ValueError:
        raise FormatProblem("This answer should be an IP address, such as 10.42.85.10.")


_TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%m/%d/%Y %I:%M:%S %p", "%Y%m%dT%H%M%S",
)


def _timestamp(s):
    t = s.strip()
    if re.fullmatch(r"\d{9,11}", t):                     # Unix seconds
        return _dt.datetime.fromtimestamp(int(t), _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t = re.sub(r"\s*\(?\b(UTC|GMT|Z)\b\)?\s*$", "+00:00", t, flags=re.I)
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    t = re.sub(r"(\d{2}:\d{2}:\d{2})[.,]\d+", r"\1", t)   # drop fractions of a second
    tz = None
    m = re.search(r"([+-])(\d{2}):?(\d{2})$", t)
    if m and re.search(r"\d{2}:\d{2}", t[: m.start()]):
        sign = 1 if m.group(1) == "+" else -1
        hours, minutes = int(m.group(2)), int(m.group(3))
        if hours > 23 or minutes > 59:
            raise FormatProblem("The time-zone offset %s%s:%s is not possible; offsets go from "
                                "-23:59 to +23:59, for example +02:00." % m.groups())
        tz = _dt.timezone(sign * _dt.timedelta(hours=hours, minutes=minutes))
        t = t[: m.start()].strip()
    for fmt in _TS_FORMATS:
        try:
            d = _dt.datetime.strptime(t, fmt)
            break
        except ValueError:
            continue
    else:
        raise FormatProblem("This answer should be a date and time in UTC, such as "
                            "2020-09-19 03:21:46 (times without a zone are read as UTC).")
    try:
        d = d.replace(tzinfo=tz or _dt.timezone.utc).astimezone(_dt.timezone.utc)
    except (ValueError, OverflowError):
        raise FormatProblem("This date and time cannot be converted to UTC. Give it in UTC, "
                            "such as 2020-09-19 03:21:46.")
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize(rule, answer, pattern=None, then=None):
    if answer is None:
        answer = ""
    if rule == "exact":
        return answer.strip()
    if rule == "casefold":
        return re.sub(r"\s+", " ", answer.strip()).casefold()
    if rule == "integer":
        return _integer(answer)
    if rule == "ip":
        return _ip(answer)
    if rule == "timestamp-utc-seconds":
        return _timestamp(answer)
    if rule == "regex-extract":
        if not pattern:
            raise ValueError("regex-extract needs a pattern")
        m = re.search(pattern, answer)
        if not m:
            raise FormatProblem("The answer does not contain the kind of value this "
                                "checkpoint asks for.")
        value = m.group(1) if m.groups() and m.group(1) is not None else m.group(0)
        value = value.strip()
        if then and then != "regex-extract":
            return normalize(then, value)
        return value
    raise ValueError("unknown normalisation rule %r" % rule)
