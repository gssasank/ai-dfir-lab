#!/bin/sh
# dfir-lab installer / uninstaller (POSIX sh). Safe to run more than once.
#
#   sudo ./install.sh --user sansforensics [--conf path/to/lab.conf]
#   sudo ./install.sh --user sansforensics --uninstall [--purge]
#
# --root DIR stages everything under DIR instead of / (used by the tests; no
# root rights needed when DIR is writable and --user is the current user).
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
ROOT=""
TARGET_USER=""
CONF=""
MODE="install"
PURGE=0

usage() {
    cat <<'EOF'
Usage:
  install.sh --user NAME [--conf FILE] [--root DIR]        install or update
  install.sh --user NAME --uninstall [--purge] [--root DIR]

--purge        with --uninstall: also delete the student's key and ~/dfir-lab
EOF
}

die() { echo "install.sh: $*" >&2; exit "${2:-3}"; }
say() { echo "  $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --user) [ $# -ge 2 ] || die "--user needs a value" 2; TARGET_USER=$2; shift 2 ;;
        --conf) [ $# -ge 2 ] || die "--conf needs a value" 2; CONF=$2; shift 2 ;;
        --root) [ $# -ge 2 ] || die "--root needs a value" 2; ROOT=${2%/}; shift 2 ;;
        --uninstall) MODE="uninstall"; shift ;;
        --purge) PURGE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" 2 ;;
    esac
done

[ -n "$TARGET_USER" ] || { usage >&2; die "--user is required" 2; }

ENTRY=$(getent passwd "$TARGET_USER" 2>/dev/null || true)
[ -n "$ENTRY" ] || die "no such user: $TARGET_USER"
USER_HOME=$(echo "$ENTRY" | cut -d: -f6)
USER_GROUP=$(id -gn "$TARGET_USER")
HOME_DIR="$ROOT$USER_HOME"

IS_ROOT=0
[ "$(id -u)" -eq 0 ] && IS_ROOT=1
if [ -z "$ROOT" ] && [ "$IS_ROOT" -ne 1 ]; then
    die "run with sudo (or use --root DIR to stage into a directory)"
fi

BIN="$ROOT/usr/local/bin"
LIB="$ROOT/usr/local/lib/dfir-lab"
ETC="$ROOT/etc/dfir-lab"
APPS="$ROOT/usr/local/share/applications"
DESKTOP="$HOME_DIR/Desktop"
SHORTCUTS="dfir-lab-setup-key dfir-lab-start dfir-lab-check dfir-lab-replay"
STUDENT_BINS="dfir-lab dfir-lab-configure-claude"

PY=$(command -v python3 || true)
[ -n "$PY" ] || die "python3 is required"

# Run a command as the target user with HOME set to the (staged) home.
as_user() {
    if [ "$IS_ROOT" -eq 1 ] && [ "$TARGET_USER" != "root" ]; then
        if command -v runuser >/dev/null 2>&1; then
            runuser -u "$TARGET_USER" -- env HOME="$HOME_DIR" "$@"
        else
            su -s /bin/sh "$TARGET_USER" -c "HOME='$HOME_DIR' $*"
        fi
    else
        env HOME="$HOME_DIR" "$@"
    fi
}

own() {  # chown to the user when we are root
    if [ "$IS_ROOT" -eq 1 ]; then chown "$TARGET_USER:$USER_GROUP" "$@"; fi
}

remove() {  # remove a path and report it
    if [ -e "$1" ] || [ -L "$1" ]; then
        rm -rf -- "$1"
        say "removed: $1"
    else
        say "not present: $1"
    fi
}

do_install() {
    echo "Installing dfir-lab for user $TARGET_USER${ROOT:+ (staged under $ROOT)}"
    mkdir -p "$BIN" "$LIB" "$ETC" "$APPS"

    rm -rf "$LIB/dfir_lab" "$LIB/libexec" "$LIB/share"
    mkdir -p "$LIB/libexec" "$LIB/share"
    cp -R "$SRC/lib/dfir_lab" "$LIB/dfir_lab"
    find "$LIB/dfir_lab" -name __pycache__ -type d -prune -exec rm -rf {} +
    cp "$SRC/libexec/key-helper" "$SRC/libexec/pretool-guard" "$LIB/libexec/"
    cp -R "$SRC/share/claude" "$SRC/share/readiness-sample" "$SRC/share/desktop" "$LIB/share/"
    chmod -R a+rX "$LIB"
    chmod 755 "$LIB/libexec/key-helper" "$LIB/libexec/pretool-guard"
    say "package and helpers -> $LIB"

    for b in $STUDENT_BINS; do
        cp "$SRC/bin/$b" "$BIN/$b"
        chmod 755 "$BIN/$b"
    done
    say "commands -> $BIN (dfir-lab, dfir-lab-configure-claude)"

    if [ -n "$CONF" ]; then
        [ -f "$CONF" ] || die "--conf file not found: $CONF"
        cp "$CONF" "$ETC/lab.conf.new"
        mv "$ETC/lab.conf.new" "$ETC/lab.conf"
        say "settings -> $ETC/lab.conf (from $CONF)"
    elif [ ! -f "$ETC/lab.conf" ]; then
        cp "$SRC/etc/lab.conf.example" "$ETC/lab.conf"
        say "settings -> $ETC/lab.conf (EXAMPLE: fill in the REQUIRED values)"
    else
        say "settings: kept existing $ETC/lab.conf"
    fi
    chmod 644 "$ETC/lab.conf"
    if [ "$IS_ROOT" -eq 1 ]; then chown root:root "$ETC/lab.conf"; fi

    # Per-student folders.
    mkdir -p "$HOME_DIR/dfir-lab/readiness" "$HOME_DIR/dfir-lab/logs"
    own "$HOME_DIR/dfir-lab" "$HOME_DIR/dfir-lab/readiness" "$HOME_DIR/dfir-lab/logs"
    mkdir -p "$HOME_DIR/.config/dfir-lab"
    chmod 700 "$HOME_DIR/.config/dfir-lab"
    own "$HOME_DIR/.config" "$HOME_DIR/.config/dfir-lab"
    say "student folders -> $HOME_DIR/dfir-lab, $HOME_DIR/.config/dfir-lab (0700)"

    # Desktop shortcuts.
    mkdir -p "$DESKTOP"
    own "$DESKTOP"
    for s in $SHORTCUTS; do
        sed "s#@BINDIR@#$BIN#g" "$SRC/share/desktop/$s.desktop" > "$APPS/$s.desktop"
        chmod 644 "$APPS/$s.desktop"
        cp "$APPS/$s.desktop" "$DESKTOP/$s.desktop"
        chmod 755 "$DESKTOP/$s.desktop"
        own "$DESKTOP/$s.desktop"
    done
    # Mark them trusted through the user's running desktop session when there
    # is one. A private dbus-run-session starts a second gvfsd-metadata that
    # writes the same metadata database as the session's own daemon; on the
    # SIFT VM (GNOME 46) the session daemon later dropped those entries, so
    # the icons went back to untrusted about a minute after installing.
    trusted=0
    runtime="${DFIR_LAB_RUNTIME_DIR:-/run/user/$(id -u "$TARGET_USER")}"
    if command -v gio >/dev/null 2>&1; then
        trusted=1
        for s in $SHORTCUTS; do
            if [ -S "$runtime/bus" ]; then
                as_user env XDG_RUNTIME_DIR="$runtime" DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime/bus" \
                    gio set "$DESKTOP/$s.desktop" metadata::trusted true >/dev/null 2>&1 || trusted=0
            elif command -v dbus-run-session >/dev/null 2>&1; then
                as_user dbus-run-session -- gio set "$DESKTOP/$s.desktop" metadata::trusted true \
                    >/dev/null 2>&1 || trusted=0
            else
                as_user gio set "$DESKTOP/$s.desktop" metadata::trusted true >/dev/null 2>&1 || trusted=0
            fi
        done
    fi
    if [ "$trusted" -eq 1 ]; then
        say "desktop shortcuts -> $DESKTOP (marked trusted)"
    else
        say "desktop shortcuts -> $DESKTOP (could NOT mark trusted: no gio/session;"
        say "   right-click each icon and choose 'Allow Launching' once)"
    fi

    # Claude Code settings (merged, backed up once).
    if as_user env DFIR_LAB_CONF="$ETC/lab.conf" "$PY" "$BIN/dfir-lab-configure-claude" \
            --libexec "$LIB/libexec"; then
        :
    else
        say "WARNING: Claude Code settings were not configured (fill in $ETC/lab.conf, then"
        say "   run dfir-lab-configure-claude as $TARGET_USER)"
    fi
    echo "Done. Next: as $TARGET_USER run 'dfir-lab setup-key', then 'dfir-lab check'."
}

do_uninstall() {
    echo "Uninstalling dfir-lab for user $TARGET_USER${ROOT:+ (staged under $ROOT)}"
    if [ -f "$BIN/dfir-lab-configure-claude" ]; then
        as_user "$PY" "$BIN/dfir-lab-configure-claude" --restore | sed 's/^/  /' || true
    fi
    for b in $STUDENT_BINS; do remove "$BIN/$b"; done
    remove "$LIB"
    remove "$ETC"
    for s in $SHORTCUTS; do
        remove "$APPS/$s.desktop"
        remove "$DESKTOP/$s.desktop"
    done
    if [ "$PURGE" -eq 1 ]; then
        remove "$HOME_DIR/.config/dfir-lab"
        remove "$HOME_DIR/dfir-lab"
    else
        say "kept: $HOME_DIR/.config/dfir-lab and $HOME_DIR/dfir-lab (use --purge to delete)"
    fi
    echo "Done."
}

case "$MODE" in
    install) do_install ;;
    uninstall) do_uninstall ;;
esac
