"""dfir-lab-configure-claude: merge the course settings into the user's
Claude Code settings file (~/.claude/settings.json, or
$CLAUDE_CONFIG_DIR/settings.json).

Merge rules (spec 6.1):
- lists are unioned without duplicates, existing order kept, ours appended;
- our `env` values and `apiKeyHelper` win;
- unrelated keys are preserved;
- the previous file is backed up once (settings.json.pre-dfir-lab);
- running it again changes nothing.
Spec 6.4 additions:
- credential and provider variables already in `env` are neutralised;
- the merge owns the guard hook entry: a copy of it with another matcher or
  failure policy is replaced by ours;
- failure to read, merge or write raises ConfigError; "nothing to change"
  is a normal result.

The guard and the deny rules deter the obvious accidental read of the key by
the agent; they are not a security boundary (the agent runs as the same user
who owns the key).

Setting names verified against code.claude.com/docs on 2026-11-13:
apiKeyHelper, env, permissions.deny (Read(~/path/**), bare WebFetch /
WebSearch), hooks.PreToolUse [{matcher, hooks:[{type, command, timeout,
onFailure}]}].
"""

import argparse
import copy
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from . import paths, ui
from .claude_env import settings_env
from .config import Config
from .errors import ConfigError, LabError
from .fsutil import write_text_atomic

BACKUP_SUFFIX = ".pre-dfir-lab"
ABSENT_MARKER_SUFFIX = ".pre-dfir-lab.absent"


BUILTIN_FRAGMENT = {
    "apiKeyHelper": "@LIBEXEC@/key-helper",
    "env": {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1"},
    "permissions": {"deny": ["Read(~/.config/dfir-lab/**)", "Read(~/.config/dfir-lab)",
                             "Edit(~/.config/dfir-lab/**)", "WebFetch", "WebSearch"]},
    "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": "@LIBEXEC@/pretool-guard", "timeout": 10,
         "onFailure": "block"}]}]},
}


def _template():
    """share/claude/settings-fragment.json, or the built-in copy."""
    p = paths.share_dir() / "claude" / "settings-fragment.json"
    try:
        with open(p, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and "apiKeyHelper" in data:
            return data
    except (OSError, ValueError):
        pass
    return copy.deepcopy(BUILTIN_FRAGMENT)


def _fill(obj, libexec):
    if isinstance(obj, str):
        return obj.replace("@LIBEXEC@", libexec)
    if isinstance(obj, list):
        return [_fill(x, libexec) for x in obj]
    if isinstance(obj, dict):
        return {k: _fill(v, libexec) for k, v in obj.items()}
    return obj


def _quote_commands(frag):
    """Claude Code runs apiKeyHelper and hook commands through a shell, so a
    path with spaces must be quoted (observed with Claude Code 2.1.322:
    an unquoted path containing a space makes the helper "fail")."""
    if isinstance(frag.get("apiKeyHelper"), str):
        frag["apiKeyHelper"] = shlex.quote(frag["apiKeyHelper"])
    for groups in (frag.get("hooks") or {}).values():
        for g in groups if isinstance(groups, list) else []:
            for h in g.get("hooks", []) if isinstance(g, dict) else []:
                if isinstance(h, dict) and isinstance(h.get("command"), str):
                    h["command"] = shlex.quote(h["command"])
    return frag


def fragment(cfg, libexec=None):
    libexec = str(Path(libexec) if libexec else paths.libexec_dir())
    frag = _quote_commands(_fill(_template(), libexec))
    env = dict(frag.get("env") or {})
    env.update(settings_env(cfg))
    frag["env"] = env
    frag.setdefault("permissions", {}).setdefault("deny", [])
    frag.setdefault("hooks", {})
    return frag


def _union(existing, ours):
    out = list(existing)
    for item in ours:
        if item not in out:
            out.append(item)
    return out




def _hook_commands(group):
    return [h.get("command") for h in group.get("hooks", []) if isinstance(h, dict)]


def _norm_cmd(cmd):
    """A hook command compared without its shell quoting, so a quoted and an
    unquoted copy of the guard path count as the same command."""
    try:
        return tuple(shlex.split(cmd))
    except ValueError:
        return (cmd,)


def _reconcile_hooks(existing, ours):
    """The merge owns the guard hook entry (spec 6.4). Any hook entry that runs
    one of our commands but is not exactly our group (other matcher, missing or
    different onFailure, timeout, ...) is removed, and our group is added once.
    Unrelated hook entries and groups are kept in their order."""
    our_cmds = {_norm_cmd(c) for g in ours for c in _hook_commands(g) if isinstance(c, str)}
    out, present = [], []
    for g in existing:
        if g in ours:
            if g not in present:
                out.append(g)
                present.append(g)
            continue
        if isinstance(g, dict) and isinstance(g.get("hooks"), list):
            kept = [h for h in g["hooks"]
                    if not (isinstance(h, dict) and isinstance(h.get("command"), str)
                            and _norm_cmd(h["command"]) in our_cmds)]
            if len(kept) != len(g["hooks"]):
                if not kept:
                    continue
                g = dict(g, hooks=kept)
        out.append(g)
    for g in ours:
        if g not in present:
            out.append(g)
    return out


def merge(current, frag):
    """Pure function: returns the merged settings dict."""
    if not isinstance(current, dict):
        current = {}
    merged = copy.deepcopy(current)
    merged["apiKeyHelper"] = frag["apiKeyHelper"]

    # Our values win. This includes the empty values that neutralise any
    # ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / CLAUDE_CODE_USE_* already in
    # the file (see claude_env.settings_env).
    env = merged.get("env")
    env = dict(env) if isinstance(env, dict) else {}
    env.update(frag["env"])
    merged["env"] = env

    perms = merged.get("permissions")
    perms = dict(perms) if isinstance(perms, dict) else {}
    for list_name, items in frag["permissions"].items():
        existing = perms.get(list_name)
        perms[list_name] = _union(existing if isinstance(existing, list) else [], items)
    merged["permissions"] = perms

    hooks = merged.get("hooks")
    hooks = dict(hooks) if isinstance(hooks, dict) else {}
    for event, groups in frag["hooks"].items():
        existing = hooks.get(event)
        hooks[event] = _reconcile_hooks(list(existing) if isinstance(existing, list) else [],
                                        groups)
    merged["hooks"] = hooks
    return merged


def is_configured(current, frag):
    return isinstance(current, dict) and merge(current, frag) == current


def _fs_error(action, path, exc):
    return ConfigError("Could not %s %s (%s)." % (action, path, exc.strerror or exc),
                       "Tell the TA; your account must be able to write this file. "
                       "Nothing else was changed.")


def _load(path):
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError("Could not read %s (%s)." % (path, getattr(exc, "strerror", None)
                                                       or exc.__class__.__name__),
                          "Tell the TA.")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        raise ConfigError("The Claude Code settings file %s is not valid JSON, so it was not "
                          "changed." % path,
                          "Tell the TA. The file may have been edited by hand.")
    if not isinstance(data, dict):
        raise ConfigError("The Claude Code settings file %s does not contain a JSON object."
                          % path, "Tell the TA.")
    return data


def apply(cfg, settings_path=None, libexec=None, dry_run=False):
    """Merge into the settings file. Returns (changed, path, merged).

    changed is False when the file already held the course settings ("nothing
    to change"). Any failure raises ConfigError (exit code 3): the file could
    not be read, is not valid JSON, or could not be written."""
    path = Path(settings_path) if settings_path else paths.claude_settings_file()
    current = _load(path)
    merged = merge(current or {}, fragment(cfg, libexec))
    if current is not None and merged == current:
        return False, path, merged
    if dry_run:
        return True, path, merged
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    marker = path.with_name(path.name + ABSENT_MARKER_SUFFIX)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists() and not marker.exists():
            if current is not None:
                shutil.copy2(path, backup)
            else:
                marker.write_text("settings.json did not exist before dfir-lab\n",
                                  encoding="utf-8")
        mode = 0o600
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            pass
        write_text_atomic(path, json.dumps(merged, indent=2) + "\n", mode=mode)
    except OSError as exc:
        raise _fs_error("update the Claude Code settings file", path, exc)
    return True, path, merged


def ensure(cfg, quiet=False):
    """Make sure the course settings are in place before Claude Code runs.

    Returns True when the file was changed (e.g. another installer had
    replaced it) and False when nothing needed changing. Raises ConfigError
    when the settings could not be read, merged or written; callers must then
    not start Claude Code (spec 6.4)."""
    changed, path, _ = apply(cfg)
    if changed and not quiet:
        ui.out("Note: the Claude Code settings in %s were missing the course entries and "
               "have been restored." % path)
    return changed


def trust_case_folder(cfg, global_config=None):
    """Pre-accept Claude Code's workspace-trust question for the case folder
    (spec 6.4), so a student's first interactive start lands at the prompt.

    Sets projects["<case dir>"].hasTrustDialogAccepted = true in Claude Code's
    global config (~/.claude.json, or $CLAUDE_CONFIG_DIR/.claude.json). The
    docs describe this key for trusting a folder by hand
    (code.claude.com/docs/en/permissions, "What runs before you trust a
    folder"); the file itself is Claude Code's own and otherwise undocumented.
    Everything else in the file is kept.

    Returns (ok, message). Never raises: if this fails the student just sees
    the trust question once, which the start banner explains."""
    gpath = Path(global_config) if global_config else paths.claude_global_config()
    try:
        case = cfg.case_dir
    except LabError as exc:
        return False, exc.what
    keys = [str(case)]
    real = os.path.realpath(str(case))
    if real not in keys:
        keys.append(real)
    try:
        text = gpath.read_text(encoding="utf-8") if gpath.exists() else ""
    except (OSError, UnicodeDecodeError) as exc:
        return False, "could not read %s (%s)" % (gpath, exc.__class__.__name__)
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return False, "%s is not valid JSON, so it was not changed" % gpath
    projects = data.get("projects", {}) if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return False, "%s does not have the expected layout, so it was not changed" % gpath
    changed = False
    for k in keys:
        entry = projects.get(k)
        entry = dict(entry) if isinstance(entry, dict) else {}
        if entry.get("hasTrustDialogAccepted") is not True:
            entry["hasTrustDialogAccepted"] = True
            projects[k] = entry
            changed = True
    if not changed:
        return True, "the case folder is already trusted in %s" % gpath
    data["projects"] = projects
    mode = 0o600
    try:
        mode = gpath.stat().st_mode & 0o777
    except OSError:
        pass
    try:
        write_text_atomic(gpath, json.dumps(data, indent=2) + "\n", mode=mode)
    except OSError as exc:
        return False, "could not write %s (%s)" % (gpath, exc.strerror or exc)
    return True, "marked the case folder %s as trusted in %s" % (case, gpath)


def restore(settings_path=None):
    """Put back the backup taken before the first merge."""
    path = Path(settings_path) if settings_path else paths.claude_settings_file()
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    marker = path.with_name(path.name + ABSENT_MARKER_SUFFIX)
    try:
        if backup.exists():
            os.replace(backup, path)
            return "restored %s from %s" % (path, backup)
        if marker.exists():
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            marker.unlink()
            return "removed %s (it did not exist before dfir-lab)" % path
    except OSError as exc:
        raise _fs_error("restore the Claude Code settings file", path, exc)
    return "no backup of %s found; left unchanged" % path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dfir-lab-configure-claude",
                                 description="Merge the course settings into Claude Code's "
                                             "user settings file.")
    ap.add_argument("--settings", help="settings file (default: ~/.claude/settings.json)")
    ap.add_argument("--libexec", help="directory holding key-helper and pretool-guard")
    ap.add_argument("--conf", help="lab.conf to use (default: $DFIR_LAB_CONF or /etc/dfir-lab/lab.conf)")
    ap.add_argument("--dry-run", action="store_true", help="print the result, change nothing")
    ap.add_argument("--restore", action="store_true", help="restore the backup and exit")
    args = ap.parse_args(argv)
    try:
        if args.restore:
            ui.out(restore(args.settings))
            return 0
        cfg = Config(args.conf)
        cfg.require_all({("gateway", "base_url"), ("gateway", "model_main")})
        changed, path, merged = apply(cfg, args.settings, args.libexec, args.dry_run)
    except LabError as exc:
        ui.problem(exc.what, exc.todo)
        return exc.code
    except OSError as exc:   # anything the specific handlers above missed
        ui.problem("Could not update the Claude Code settings (%s%s)." % (
            exc.strerror or exc.__class__.__name__,
            (": %s" % exc.filename) if getattr(exc, "filename", None) else ""),
            "Tell the TA. Nothing else was changed.")
        return 3
    if args.dry_run:
        ui.out(json.dumps(merged, indent=2))
        return 0
    if changed:
        ui.out("Updated Claude Code settings: %s" % path)
    else:
        ui.out("Claude Code settings already up to date: %s" % path)
    if cfg.get("case", "dir"):
        ok, msg = trust_case_folder(cfg)
        ui.out(("First start: %s" if ok else "Note: could not pre-accept the folder trust "
                "question (%s); Claude Code will ask once.") % msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
