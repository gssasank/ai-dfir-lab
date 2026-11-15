"""Student-facing text for every gateway outcome.

Each outcome has a distinct "what happened" sentence and a "what to do"
sentence. "Cannot reach the gateway" (dns, timeout, refused, unreachable,
cert, tls) and "key rejected" are deliberately worded so they cannot be
confused.
"""

from .redact import redact

OUTCOMES = {
    # --- cannot reach the gateway ------------------------------------
    "dns": (
        "Cannot reach the gateway: your computer could not look up the name {host}. "
        "This is a network problem, not a problem with your key.",
        "{hint} If you are on the Duke network already, check that the VM has internet "
        "access (open any web page inside the VM).",
    ),
    "timeout": (
        "Cannot reach the gateway: {host} did not answer within {timeout:g} seconds. "
        "This usually means you are not on the Duke network. Your key was not tested.",
        "{hint}",
    ),
    "refused": (
        "Cannot reach the gateway: {host} refused the connection on port {port}. "
        "This is a network or server problem, not a problem with your key.",
        "{hint} If it still fails, {contact}.",
    ),
    "unreachable": (
        "Cannot reach the gateway: the network has no route to {host} ({detail}). "
        "This is a network problem, not a problem with your key.",
        "{hint} Check that the VM's network adapter is set to NAT and is connected.",
    ),
    "cert": (
        "Cannot reach the gateway safely: the secure connection to {host} failed its "
        "certificate check ({detail}). Your key was not sent.",
        "Check that the date and time in the VM are correct, then try again. If you are on "
        "a hotel, cafe or company network that inspects traffic, use the Duke VPN. "
        "If it still fails, {contact}.",
    ),
    "tls": (
        "Cannot reach the gateway safely: the secure connection to {host} failed ({detail}). "
        "Your key was not sent.",
        "{hint} If it still fails, {contact}.",
    ),
    "slow": (
        "The gateway accepted the connection but did not answer within {rtimeout:g} seconds.",
        "The gateway may be busy. Wait a minute and try again. If it keeps happening, {contact}.",
    ),
    "closed": (
        "The connection to the gateway broke before an answer arrived ({detail}).",
        "Try again. If it keeps happening, a firewall or proxy may be interfering; {contact}.",
    ),
    # --- the gateway answered -----------------------------------------
    "key_rejected": (
        "The gateway rejected your key (HTTP {status}). The network is fine; the key is "
        "wrong, expired, or was copied incompletely.",
        "Copy your key again from the Duke AI dashboard{dashboard} and run: dfir-lab setup-key",
    ),
    "model_forbidden": (
        "Your key works, but the gateway says the course model {model} is not available to "
        "your key, or that no model has that name (HTTP {status}).",
        "This is not something you can fix yourself. {contact_cap} and say that your key "
        "cannot use {model}.",
    ),
    "model_unknown": (
        "Your key works, but the gateway does not offer the course model {model} (HTTP {status}).",
        "This is a course configuration problem, not your mistake. {contact_cap} and say "
        "that the gateway does not know {model}.",
    ),
    "model_missing": (
        "Your key works, but the course model {model} is not in the list of models your key "
        "may use.{similar}",
        "{contact_cap} and say that {model} is not available to your key.",
    ),
    "rate_limit": (
        "The gateway is holding your key back for a moment (HTTP {status}): it allows each key "
        "{per_minute}, and you went over that in the last minute.{counted} Every Claude Code "
        "request counts the whole conversation so far, so a long conversation reaches this "
        "quickly. This is not your budget and not a problem with your key.",
        "Wait one minute and try again. Inside Claude Code this is retried by itself; if "
        "Claude Code stops with 'Request rejected (429)', wait a minute and send your message "
        "again. Typing /clear to start a fresh conversation makes every request smaller.",
    ),
    "budget": (
        "Your key has used up its budget{budget} (HTTP {status}). The gateway refuses further "
        "requests until the budget resets.",
        "Wait until the reset time and try again. Meanwhile you can use the offline replay: "
        "dfir-lab replay. If you have hardly used the AI, {contact}.",
    ),
    "limit": (
        "The gateway refused the request because a usage limit of your key was reached "
        "(HTTP {status}). Your key has per-minute limits{per_minute_paren} and a budget{budget}; "
        "the gateway did not say which one.{retry}",
        "Wait one minute and try again. If it still fails, the budget is probably used up "
        "until it resets; {contact} if you have hardly used the AI.",
    ),
    "busy": (
        "The AI service behind the gateway is busy or temporarily down (HTTP {status}). This is "
        "not your usage limit and not a problem with your key.{retry}",
        "Wait a few minutes and try again. If it keeps happening, {contact}.",
    ),
    "request_rejected": (
        "The gateway rejected part of the request (HTTP {status}): it does not accept a setting "
        "or field that was sent. Your key and the network are fine. The gateway said: {body}",
        "This is a gateway or course configuration problem, not your mistake. {contact_cap} "
        "and include this message.",
    ),
    "upstream_auth": (
        "The gateway answered HTTP {status} (authentication failed), but the error seems to come "
        "from the AI provider behind the gateway rather than from your key. dfir-lab cannot "
        "tell this for certain. The gateway said: {body}",
        "If 'Gateway login' passed in dfir-lab check, your key is probably fine: {contact} and "
        "include this message. Otherwise copy your key again and run: dfir-lab setup-key",
    ),
    "unclear_404": (
        "The gateway answered HTTP 404 (not found) without saying whether the Messages route or "
        "the course model {model} is missing, so dfir-lab cannot tell which. The gateway said: "
        "{body}",
        "This is not something you can fix. {contact_cap} and include this message.",
    ),
    "unexpected_page": (
        "The gateway address answered (HTTP {status}), but with a web page or other unexpected "
        "content instead of a list of models, so your key was not confirmed. A Wi-Fi login "
        "page, VPN portal, or proxy may be in the way. It sent: {body}",
        "{hint} If a web browser in the VM shows a login page, complete it, then try again. "
        "If it still fails, {contact}.",
    ),
    "server_error": (
        "The gateway had an internal error (HTTP {status}). This is not your fault.",
        "Wait a minute and try again. If it keeps happening, {contact}.",
    ),
    "no_anthropic_route": (
        "The gateway answered, but it has no Anthropic Messages route (/v1/messages returned "
        "HTTP 404). Claude Code needs that route, so it cannot work through this gateway yet.",
        "This is not something you can fix. {contact_cap}. Meanwhile you can use the offline "
        "replay: dfir-lab replay",
    ),
    "models_404": (
        "The gateway does not offer a model list (/v1/models returned HTTP 404). That is "
        "allowed; the next checks test the model directly.",
        "Nothing to do unless the next checks fail.",
    ),
    "bad_reply": (
        "The gateway answered, but the reply is not in the Anthropic format Claude Code "
        "expects ({detail}).",
        "This is a gateway configuration problem, not your mistake. {contact_cap}.",
    ),
    "stream_buffered": (
        "The gateway (or a proxy in between) holds back streamed replies and sends them all "
        "at once. Claude Code stalls on long answers when this happens.",
        "If you use a company or hotel network, try the Duke VPN or another network. "
        "Otherwise {contact}.",
    ),
    "stream_dropped": (
        "The streamed reply was cut off before it finished ({detail}). Something between you "
        "and the gateway is dropping long connections.",
        "Try again. If it happens again, try the Duke VPN or another network, then {contact}.",
    ),
    "other": (
        "The gateway returned an error that dfir-lab does not recognise, so it cannot tell the "
        "cause (HTTP {status}). The gateway said: {body}",
        "Try again in a minute. If it keeps happening, {contact} and include this message.",
    ),
}

CANNOT_REACH = {"dns", "timeout", "refused", "unreachable", "cert", "tls"}


def render(kind, cfg=None, **fields):
    """Return (what, todo) for an outcome kind, redacted."""
    what_t, todo_t = OUTCOMES.get(kind, OUTCOMES["other"])
    contact = cfg.contact_line() if cfg is not None else "tell the TA"
    values = {
        "host": "", "port": "", "detail": "", "status": "", "model": "", "body": "",
        "timeout": 8.0, "rtimeout": 60.0, "similar": "", "retry": "",
        "per_minute": "a limited number of requests and tokens per minute", "counted": "",
        "budget": "", "per_minute_paren": "",
        "hint": "", "dashboard": "",
        "contact": contact,
        "contact_cap": contact[:1].upper() + contact[1:],
    }
    if cfg is not None:
        try:
            values["hint"] = cfg.network_hint
            values["timeout"] = cfg.connect_timeout
            values["rtimeout"] = cfg.request_timeout
            values["model"] = cfg.get("gateway", "model_main")
            d = cfg.dashboard_url
            values["dashboard"] = (" (%s)" % d) if d else ""
        except Exception:  # noqa: BLE001 - messages must never crash
            pass
    values.update({k: v for k, v in fields.items() if v is not None})
    if values.get("body"):
        # Redact the whole body before shortening it, so a key cut in half at
        # the limit cannot slip past the redactor.
        values["body"] = " ".join(redact(str(values["body"])).split())[:300]
    if values.get("detail"):
        values["detail"] = redact(str(values["detail"]))
    if values.get("retry"):
        values["retry"] = " The gateway says to retry after %s seconds." % values["retry"]
    try:
        what = what_t.format(**values)
        todo = todo_t.format(**values)
    except (KeyError, ValueError):
        what, todo = what_t, todo_t
    return redact(what.strip()), redact(todo.strip())


def limit_fields(limits):
    """Template fields that describe the key's real limits (gateway.KeyLimits)."""
    if limits is None:
        return {}
    f = {}
    pm = limits.per_minute_text()
    if pm:
        f["per_minute"] = pm
        f["per_minute_paren"] = " (%s)" % pm
    if limits.current_tpm is not None:
        f["counted"] = " It counted %s tokens for your key in the current minute." % format(
            limits.current_tpm, ",")
    bt = limits.budget_text()
    if bt:
        f["budget"] = " of " + bt
    return f


def outcome_fields(outcome, net=None):
    f = {"status": outcome.status or "", "detail": outcome.detail, "body": outcome.detail,
         "retry": outcome.retry_after}
    f.update(limit_fields(getattr(outcome, "limits", None)))
    if net is not None:
        f.update(host=net.host, port=net.port)
    return f
