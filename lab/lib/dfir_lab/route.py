"""Which way Claude Code reaches a model: the route.

- "duke" (default): the student's Duke AI Gateway key, through the gateway.
- "subscription" (optional): the student's own Claude subscription (Pro, Max
  or higher), signed in to Claude Code directly with Anthropic.

The choice lives in the student's own lab state (~/dfir-lab/route.json), not
in the root-owned lab.conf, so it survives reboots and re-running the setup
script. A missing, unreadable or unknown file means the Duke route.
"""

import json

from . import fsutil, paths

DUKE = "duke"
SUBSCRIPTION = "subscription"
ROUTES = (DUKE, SUBSCRIPTION)
DEFAULT = DUKE

LABELS = {
    DUKE: "your Duke AI Gateway key",
    SUBSCRIPTION: "your own Claude subscription",
}


def current():
    """The stored route, or the Duke route when nothing valid is stored."""
    try:
        with open(paths.route_file(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return DEFAULT
    value = data.get("route") if isinstance(data, dict) else None
    return value if value in ROUTES else DEFAULT


def is_subscription(name=None):
    return (name or current()) == SUBSCRIPTION


def resolve(name=None):
    """`name` if given and valid, otherwise the stored route."""
    if name is None:
        return current()
    if name not in ROUTES:
        raise ValueError("unknown route %r" % name)
    return name


def save(name):
    """Store the route (atomic write). Raises OSError when it cannot be written."""
    if name not in ROUTES:
        raise ValueError("unknown route %r" % name)
    fsutil.write_json_atomic(paths.route_file(), {"route": name, "changed": fsutil.utc_iso()})
    return paths.route_file()
