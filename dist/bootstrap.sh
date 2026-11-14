#!/usr/bin/env bash
# CYBERSEC 510 DFIR lab setup script.
#
# Turns an untouched SANS SIFT Workstation VM into a ready lab machine:
# Claude Code (pinned version), Protocol SIFT, the dfir-lab toolkit, the case
# files, and a few desktop settings. Safe to run any number of times.
#
#   bash bootstrap.sh [--yes] [--kit PATH]
#   bash bootstrap.sh --uninstall [--purge] [--yes]
#
# GENERATED FILE: dist/bootstrap.sh is built by bootstrap/build.py from
# bootstrap/bootstrap.sh.in, bootstrap/release.conf, the toolkit in lab/ and
# bootstrap/case-overlay/. The compressed payload at the end of the file holds
# the toolkit and the case overlay. Edit the sources, not this file.
#
# Exit codes: 0 setup complete; 1 setup incomplete (the summary says why and
# what to do; running again resumes); 2 refused (usage, run as root, not
# confirmed); 3 stopped by an error.

set -u -o pipefail
umask 022

# ---- release settings (filled in by build.py) ------------------------------
R_VERSION='0.3.0'
R_CLAUDE_CODE_VERSION='2.1.322'
R_PROTOCOL_SIFT_REPO='https://github.com/teamdfir/protocol-sift.git'
R_PROTOCOL_SIFT_COMMIT='40bed7a96bfd986ea048c3b2aeb9d788b2f3400c'
R_GATEWAY_BASE_URL='https://litellm.oit.duke.edu'
R_CASE_ID='szechuan'
R_CASE_TITLE='Szechuan Sauce'
R_CASE_KIT_PATTERN='szechuan-kit-*.zip'
R_CASE_KIT_SHA256='b7157b32995e8e323e7d2d3bf8f214cae1e029c6a6ae5ca8981319194051d42a'
R_PAYLOAD_SHA256='7c887371fa03e1b89adbc109611b8d30df469b4650cc8e438d0794362cc2e99d'
# -----------------------------------------------------------------------------

PAYLOAD_MARKER="__DFIR_LAB_PAYLOAD""_BELOW__"
CASE_DIR="/cases/$R_CASE_ID"
STATE_DIR="$HOME/dfir-lab"
LOG_DIR="$STATE_DIR/logs"
CLAUDE_BIN="$HOME/.local/bin/claude"
PSIFT_MARKER="$HOME/.claude/.protocol-sift-install"
TOOLKIT_STAMP="/usr/local/lib/dfir-lab/.setup-stamp"
SHORTCUTS="dfir-lab-setup-key dfir-lab-start dfir-lab-check dfir-lab-replay"
STUDENT_DIRS="analysis reports exports .claude"
BROWSER_DESKTOP="chromium_chromium.desktop"
BASHRC_BEGIN="# >>> dfir-lab setup (CYBERSEC 510) >>>"
BASHRC_END="# <<< dfir-lab setup (CYBERSEC 510) <<<"
TOTAL_STEPS=8
MIN_FREE_GB=3

SELF=""
LOG=""
WORK=""
PAYLOAD=""
YES=0
KIT_ARG=""
MODE="setup"
PURGE=0
CHANGED=0
CLAUDE_OK=0
PSIFT_OK=0
TOOLKIT_OK=0
CASE_OK=0
CLAUDE_FOUND=""
INCOMPLETE=()
WARNINGS=()
NOTES=()
STEP_START=0
STEP_RX=0

# ---------------------------------------------------------------------------
# Output. The screen gets one line per step plus short results; the log gets
# everything, including the full output of every command.
# ---------------------------------------------------------------------------

out() {
    printf '%s\n' "$*"
    if [ -n "$LOG" ]; then printf '%s\n' "$*" >> "$LOG"; fi
}
log() { if [ -n "$LOG" ]; then printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >> "$LOG"; fi; }
ok()   { out "      ok: $*"; }
done_already() { out "      already done: $*"; }
warn() { out "      WARNING: $*"; WARNINGS+=("$*"); }
note() { out "      note: $*"; }

# incomplete "what is not done" "what the student should do"
incomplete() {
    out "      NOT DONE: $1"
    INCOMPLETE+=("$1|$2")
}

# Run a command with all its output going to the log only.
run() {
    log "+ $*"
    "$@" >> "$LOG" 2>&1 < /dev/null
    local rc=$?
    [ "$rc" -eq 0 ] || log "  (exit code $rc)"
    return "$rc"
}

changed() { CHANGED=$((CHANGED + 1)); }

rx_bytes() {
    local total=0 f n
    for f in /sys/class/net/*/statistics/rx_bytes; do
        case "$f" in */lo/*|*/docker*|*/veth*|*/br-*) continue ;; esac
        read -r n < "$f" 2>/dev/null || continue
        total=$((total + n))
    done
    echo "$total"
}

step() {
    end_step
    out "[$1/$TOTAL_STEPS] $2"
    log "---- step $1: $2"
    STEP_START=$SECONDS
    STEP_RX=$(rx_bytes)
    STEP_ACTIVE=1
}
STEP_ACTIVE=0
end_step() {
    if [ "$STEP_ACTIVE" -eq 1 ]; then
        local mb=$(( ($(rx_bytes) - STEP_RX) / 1048576 ))
        log "---- step took $((SECONDS - STEP_START)) s, network received about $mb MB"
    fi
    STEP_ACTIVE=0
}

die() {  # stop with an error (exit 3)
    out ""
    out "SETUP STOPPED: $1"
    [ -n "${2:-}" ] && out "What to do: $2"
    [ -n "$LOG" ] && out "Log: $LOG"
    exit 3
}

cleanup() {
    if [ -n "$WORK" ] && [ -d "$WORK" ]; then rm -rf "$WORK" 2>/dev/null; fi
    sudo -n rm -rf "/cases/.$R_CASE_ID.staging" "/cases/.$R_CASE_ID.unzip" \
        "/cases/.$R_CASE_ID.old" 2>/dev/null || true
}

on_interrupt() {
    out ""
    out "Setup interrupted. Nothing is broken: run the same command again to continue."
    [ -n "$LOG" ] && out "Log: $LOG"
    exit 130
}

usage() {
    cat <<EOF
CYBERSEC 510 DFIR lab setup, version $R_VERSION

  bash bootstrap.sh [--yes] [--kit PATH]
  bash bootstrap.sh --uninstall [--purge] [--yes]

  --kit PATH    use this case kit zip (default: look for $R_CASE_KIT_PATTERN
                in ~/Downloads, ~/Desktop and your home folder)
  --yes         answer yes to questions (for automation)
  --uninstall   remove the lab toolkit, the case folder and the .bashrc
                change; keep Claude Code, Protocol SIFT, your Duke AI key and
                your work (analysis/, reports/, exports/)
  --purge       with --uninstall: also remove Claude Code, ~/.claude
                (Protocol SIFT), your Duke AI key, ~/dfir-lab and your work
EOF
}

# ---------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --yes|-y) YES=1; shift ;;
            --kit) [ $# -ge 2 ] || { echo "--kit needs a file name" >&2; exit 2; }
                   KIT_ARG=$2; shift 2 ;;
            --kit=*) KIT_ARG=${1#--kit=}; shift ;;
            --uninstall) MODE="uninstall"; shift ;;
            --purge) PURGE=1; shift ;;
            --version) echo "$R_VERSION"; exit 0 ;;
            -h|--help) usage; exit 0 ;;
            *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
        esac
    done
    if [ "$PURGE" -eq 1 ] && [ "$MODE" != "uninstall" ]; then
        echo "--purge only works together with --uninstall" >&2; exit 2
    fi
    if [ -n "$KIT_ARG" ]; then
        case "$KIT_ARG" in /*) ;; *) KIT_ARG="$PWD/$KIT_ARG" ;; esac
    fi
}

refuse_root() {
    if [ "$(id -u)" -eq 0 ]; then
        echo "Please do not run this with sudo or as root."
        echo "Run it as yourself (the sansforensics user); it asks for administrator"
        echo "rights itself where it needs them:"
        echo "    bash bootstrap.sh"
        exit 2
    fi
}

start_log() {
    # --purge deletes ~/dfir-lab, so its log goes to /tmp.
    if [ "$MODE" = "uninstall" ] && [ "$PURGE" -eq 1 ]; then LOG_DIR=/tmp; fi
    mkdir -p "$LOG_DIR" "$STATE_DIR" 2>/dev/null || { echo "Cannot create $LOG_DIR"; exit 3; }
    LOG="$LOG_DIR/bootstrap-$(date -u +%Y%m%dT%H%M%SZ).log"
    : > "$LOG" || { echo "Cannot write the log $LOG"; exit 3; }
    # Anything bash itself complains about also lands in the log.
    exec 2> >(tee -a "$LOG" >&2)
    log "bootstrap.sh $R_VERSION ($MODE) started by $(id -un) on $(hostname) in $PWD"
    log "script: $SELF"
    log "release: claude=$R_CLAUDE_CODE_VERSION protocol-sift=$R_PROTOCOL_SIFT_COMMIT case=$R_CASE_ID gateway=$R_GATEWAY_BASE_URL"
}

take_lock() {
    exec 9> "$STATE_DIR/.setup.lock"
    if ! flock -n 9; then
        out "Another copy of the lab setup is already running. Wait for it to finish."
        exit 2
    fi
}

extract_payload() {
    WORK=$(mktemp -d /tmp/dfir-lab-setup.XXXXXXXX) || die "cannot create a temporary folder"
    local line
    line=$(grep -n -m1 -x -F "$PAYLOAD_MARKER" "$SELF" | cut -d: -f1)
    [ -n "$line" ] || die "this copy of bootstrap.sh is incomplete." \
        "Download it again with the command from the course page."
    if ! tail -n +"$((line + 1))" "$SELF" | base64 -d > "$WORK/payload.tgz" 2>> "$LOG"; then
        die "this copy of bootstrap.sh is damaged." "Download it again with the command from the course page."
    fi
    local sum
    sum=$(sha256sum < "$WORK/payload.tgz" | cut -c1-64)
    if [ "$sum" != "$R_PAYLOAD_SHA256" ]; then
        die "this copy of bootstrap.sh is damaged (its contents do not match)." \
            "Download it again with the command from the course page."
    fi
    tar -xzf "$WORK/payload.tgz" -C "$WORK" 2>> "$LOG" || die "could not unpack bootstrap.sh."
    PAYLOAD="$WORK/payload"
    log "payload unpacked to $PAYLOAD (sha256 $sum)"
}

session_env() {
    # Desktop settings need the student's desktop session (D-Bus). A terminal
    # on the desktop has it; supply it when started some other way.
    local uid
    uid=$(id -u)
    [ -n "${XDG_RUNTIME_DIR:-}" ] || export XDG_RUNTIME_DIR="/run/user/$uid"
    if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "$XDG_RUNTIME_DIR/bus" ]; then
        export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
    fi
    # Without the session's XDG_DATA_DIRS, GIO does not see snap applications
    # such as Chromium (/var/lib/snapd/desktop).
    if [ -z "${XDG_DATA_DIRS:-}" ]; then
        export XDG_DATA_DIRS="/usr/share/ubuntu:/usr/share/gnome:/usr/local/share/:/usr/share/:/var/lib/snapd/desktop"
    fi
    [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "$XDG_RUNTIME_DIR/bus" ]
}

# ---------------------------------------------------------------------------
# Step 1: preflight
# ---------------------------------------------------------------------------

preflight() {
    step 1 "Checking this computer"
    local problems=() user os_id="" os_ver="" arch
    user=$(id -un)
    arch=$(uname -m)
    if [ -r /etc/os-release ]; then
        os_id=$(. /etc/os-release && echo "${ID:-}")
        os_ver=$(. /etc/os-release && echo "${VERSION_ID:-}")
    fi
    [ "$user" = "sansforensics" ] || problems+=("you are logged in as '$user', not 'sansforensics'")
    [ "$os_id" = "ubuntu" ] && [ "$os_ver" = "24.04" ] || \
        problems+=("the system is '${os_id:-unknown} ${os_ver:-}', not Ubuntu 24.04")
    [ "$arch" = "x86_64" ] || problems+=("the processor type is '$arch', not x86-64")
    log "user=$user os=$os_id $os_ver arch=$arch"
    if [ ${#problems[@]} -gt 0 ]; then
        out "      This does not look like the SANS SIFT Workstation VM the lab is made for:"
        local p
        for p in "${problems[@]}"; do out "        - $p"; done
        if [ "$YES" -eq 1 ]; then
            warn "continuing anyway because --yes was given"
        elif [ -t 0 ]; then
            local ans=""
            read -r -p "      Continue anyway? [y/N] " ans
            log "answer: $ans"
            case "$ans" in [Yy]*) warn "continuing on a VM that is not the expected SIFT VM" ;;
                *) out "      Stopped. Nothing was changed."; exit 2 ;; esac
        else
            out "      Stopped. Nothing was changed. (Add --yes to continue anyway.)"
            exit 2
        fi
    fi

    # Administrator rights (no password on the SIFT VM).
    if ! sudo -n true 2>/dev/null; then
        if [ -t 0 ]; then
            out "      This setup needs administrator rights. Enter your password if asked."
            sudo -v || die "administrator rights are needed." "Run the command again and enter your password."
        else
            die "administrator rights are needed and sudo asks for a password." \
                "Run the command in a terminal on the VM desktop."
        fi
    fi

    # Disk space.
    local free_kb free_gb
    free_kb=$(df -Pk / | awk 'NR==2 {print $4}')
    free_gb=$((free_kb / 1048576))
    log "free space on /: $free_gb GB"
    if [ "$free_gb" -lt "$MIN_FREE_GB" ]; then
        die "only $free_gb GB of disk space is free; the setup needs at least $MIN_FREE_GB GB." \
            "Delete large files you do not need, then run the command again."
    fi

    # Network: the hosts the setup downloads from. A failure is reported here
    # and again by the step that needs the host.
    local h code unreachable=()
    for h in claude.ai downloads.claude.ai github.com; do
        code=$(curl -s -o /dev/null -I --connect-timeout 8 -m 15 -w '%{http_code}' "https://$h/" 2>/dev/null)
        log "network probe https://$h/ -> HTTP ${code:-000}"
        [ "${code:-000}" != "000" ] || unreachable+=("$h")
    done
    if [ ${#unreachable[@]} -gt 0 ]; then
        warn "cannot reach ${unreachable[*]}; downloads will fail. Check that the VM has internet (VMware network: NAT)."
    fi
    out "      passed: SIFT VM (${os_id:-?} ${os_ver:-?}, $arch, user $user), $free_gb GB free$([ ${#unreachable[@]} -eq 0 ] && echo ', internet reachable')"
}

# ---------------------------------------------------------------------------
# Step 2: system packages
# ---------------------------------------------------------------------------

wait_apt_lock() {
    local waited=0
    while sudo fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock \
            /var/cache/apt/archives/lock > /dev/null 2>&1; do
        [ "$waited" -eq 0 ] && out "      waiting for another software update on this VM to finish..."
        sleep 5
        waited=$((waited + 5))
        [ "$waited" -ge 600 ] && return 1
    done
    return 0
}

packages() {
    step 2 "Installing missing system packages (copy and paste support)"
    local need=()
    dpkg-query -W -f='${Status}' open-vm-tools-desktop 2>/dev/null | grep -q "install ok installed" \
        || need+=(open-vm-tools-desktop)
    command -v zenity > /dev/null 2>&1 || need+=(zenity)
    if [ ${#need[@]} -eq 0 ]; then
        done_already "open-vm-tools-desktop and zenity are installed"
        return
    fi
    log "missing packages: ${need[*]}"
    if ! wait_apt_lock; then
        warn "another software update kept running for 10 minutes; skipped ${need[*]}"
        return
    fi
    if ! run sudo env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 update; then
        warn "could not refresh the package lists (apt-get update failed); trying anyway"
    fi
    local p
    for p in "${need[@]}"; do
        if run sudo env DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=600 \
                install -y --no-install-recommends "$p"; then
            changed
            if [ "$p" = "open-vm-tools-desktop" ]; then
                ok "installed open-vm-tools-desktop (copy and paste works after the VM restarts)"
                NOTES+=("Copy and paste between your computer and the VM starts working after you restart the VM.")
            else
                ok "installed $p"
            fi
        else
            if [ "$p" = "open-vm-tools-desktop" ]; then
                warn "could not install open-vm-tools-desktop: copy and paste between your computer and the VM will not work"
            else
                warn "could not install $p: the lab tools will ask questions in the terminal instead of in windows"
            fi
        fi
    done
}

# ---------------------------------------------------------------------------
# Step 3: Claude Code
# ---------------------------------------------------------------------------

claude_version() {  # prints X.Y.Z of the claude at $1, or nothing
    [ -x "$1" ] || return 0
    timeout 60 "$1" --version < /dev/null 2>/dev/null | head -n1 | grep -oE '^[0-9]+\.[0-9]+\.[0-9]+' || true
}

claude_code() {
    step 3 "Installing Claude Code $R_CLAUDE_CODE_VERSION"
    local cur
    cur=$(claude_version "$CLAUDE_BIN")
    log "installed Claude Code at $CLAUDE_BIN: ${cur:-none}"
    if [ "$cur" = "$R_CLAUDE_CODE_VERSION" ]; then
        done_already "Claude Code $cur is installed ($CLAUDE_BIN)"
        CLAUDE_OK=1; CLAUDE_FOUND=$cur
        return
    fi
    [ -n "$cur" ] && out "      replacing Claude Code $cur with $R_CLAUDE_CODE_VERSION"
    local inst="$WORK/claude-install.sh"
    if ! run curl -fsSL --retry 3 --connect-timeout 15 -o "$inst" https://claude.ai/install.sh; then
        incomplete "Claude Code could not be downloaded." \
            "Check that the VM has internet, then run the same setup command again."
        return
    fi
    out "      downloading and installing (about 250 MB; this is the slowest step)..."
    run env DISABLE_AUTOUPDATER=1 bash "$inst" "$R_CLAUDE_CODE_VERSION"
    cur=$(claude_version "$CLAUDE_BIN")
    if [ "$cur" != "$R_CLAUDE_CODE_VERSION" ] && [ -x "$CLAUDE_BIN" ]; then
        log "version after the installer: ${cur:-none}; forcing $R_CLAUDE_CODE_VERSION"
        run env DISABLE_AUTOUPDATER=1 "$CLAUDE_BIN" install --force "$R_CLAUDE_CODE_VERSION"
        cur=$(claude_version "$CLAUDE_BIN")
    fi
    log "Claude Code now: ${cur:-none} -> $(readlink -f "$CLAUDE_BIN" 2>/dev/null)"
    if [ "$cur" = "$R_CLAUDE_CODE_VERSION" ]; then
        ok "Claude Code $cur installed ($CLAUDE_BIN)"
        CLAUDE_OK=1; CLAUDE_FOUND=$cur
        changed
    else
        incomplete "Claude Code $R_CLAUDE_CODE_VERSION could not be installed (found: ${cur:-nothing})." \
            "Check that the VM has internet, then run the same setup command again."
    fi
}

# ---------------------------------------------------------------------------
# Step 4: Protocol SIFT
# ---------------------------------------------------------------------------

psift_installed() {
    [ -f "$PSIFT_MARKER" ] || return 1
    [ "$(head -n1 "$PSIFT_MARKER")" = "commit=$R_PROTOCOL_SIFT_COMMIT" ] || return 1
    (cd "$HOME/.claude" && tail -n +2 "$PSIFT_MARKER" | sha256sum -c --status --strict) 2>/dev/null
}

protocol_sift() {
    step 4 "Installing Protocol SIFT (forensic instructions for Claude Code)"
    local short=${R_PROTOCOL_SIFT_COMMIT:0:7}
    if psift_installed; then
        done_already "Protocol SIFT $short is installed in ~/.claude"
        PSIFT_OK=1
        return
    fi
    if [ "$CLAUDE_OK" -ne 1 ]; then
        # Its installer would otherwise install an unpinned Claude Code.
        incomplete "Protocol SIFT was not installed because Claude Code is missing." \
            "Run the same setup command again."
        return
    fi
    local dir="$WORK/protocol-sift" head
    if ! run git clone --quiet --no-checkout "$R_PROTOCOL_SIFT_REPO" "$dir" || \
       ! run git -C "$dir" -c advice.detachedHead=false checkout --quiet "$R_PROTOCOL_SIFT_COMMIT"; then
        incomplete "Protocol SIFT could not be downloaded from GitHub." \
            "Check that the VM has internet, then run the same setup command again."
        return
    fi
    head=$(git -C "$dir" rev-parse HEAD 2>/dev/null)
    if [ "$head" != "$R_PROTOCOL_SIFT_COMMIT" ]; then
        incomplete "the Protocol SIFT download is not the expected version ($head)." \
            "Run the same setup command again; if it happens again, tell the TA."
        return
    fi
    log "Protocol SIFT checked out at $head"
    # Its installer finds Claude Code on PATH (ours), copies its files into
    # ~/.claude and replaces ~/.claude/settings.json; the toolkit step after
    # this one merges the course settings back in.
    if ! run env PATH="$HOME/.local/bin:$PATH" bash "$dir/install.sh"; then
        incomplete "the Protocol SIFT installer failed." \
            "Run the same setup command again; if it happens again, tell the TA and send the log."
        return
    fi
    local files=("CLAUDE.md") s missing=0
    cmp -s "$dir/global/CLAUDE.md" "$HOME/.claude/CLAUDE.md" || missing=1
    for s in "$dir"/skills/*/SKILL.md; do
        s=${s#"$dir/"}
        files+=("$s")
        cmp -s "$dir/$s" "$HOME/.claude/$s" || missing=1
    done
    if [ "$missing" -ne 0 ]; then
        incomplete "Protocol SIFT's files were not all installed in ~/.claude." \
            "Run the same setup command again; if it happens again, tell the TA and send the log."
        return
    fi
    { echo "commit=$R_PROTOCOL_SIFT_COMMIT"
      (cd "$HOME/.claude" && sha256sum -- "${files[@]}"); } > "$PSIFT_MARKER.tmp" \
        && mv "$PSIFT_MARKER.tmp" "$PSIFT_MARKER"
    ok "Protocol SIFT $short installed in ~/.claude (global CLAUDE.md and $(( ${#files[@]} - 1 )) skills)"
    PSIFT_OK=1
    changed
}

# ---------------------------------------------------------------------------
# Step 5: lab toolkit
# ---------------------------------------------------------------------------

toolkit_current() {  # $1 = generated lab.conf, $2 = stamp
    sudo test -f "$TOOLKIT_STAMP" || return 1
    [ "$(sudo cat "$TOOLKIT_STAMP" 2>/dev/null)" = "$2" ] || return 1
    cmp -s "$1" /etc/dfir-lab/lab.conf || return 1
    [ -x /usr/local/bin/dfir-lab ] && [ -x /usr/local/bin/dfir-lab-configure-claude ] || return 1
    local s
    for s in $SHORTCUTS; do
        [ -x "$HOME/Desktop/$s.desktop" ] || return 1
        cmp -s "$HOME/Desktop/$s.desktop" "/usr/local/share/applications/$s.desktop" || return 1
    done
    return 0
}

toolkit() {
    step 5 "Installing the lab toolkit (dfir-lab)"
    local conf="$WORK/lab.conf" stamp
    sed "s#@CLAUDE_BINARY@#$CLAUDE_BIN#" "$PAYLOAD/lab.conf.in" > "$conf"
    stamp="$R_PAYLOAD_SHA256 $(sha256sum < "$conf" | cut -c1-64)"
    local fresh=0
    if toolkit_current "$conf" "$stamp"; then
        done_already "dfir-lab $R_VERSION is installed (commands, desktop shortcuts, settings)"
    else
        if ! run sudo sh "$PAYLOAD/lab/install.sh" --user "$(id -un)" --conf "$conf"; then
            incomplete "the lab toolkit could not be installed." \
                "Run the same setup command again; if it happens again, tell the TA and send the log."
            return
        fi
        printf '%s\n' "$stamp" | sudo tee "$TOOLKIT_STAMP" > /dev/null
        ok "dfir-lab $R_VERSION installed (commands, desktop shortcuts, Claude Code settings)"
        changed
        fresh=1
    fi
    # Merge the course settings into ~/.claude/settings.json. This changes
    # nothing when they are already there (for example on a second run), and
    # puts them back when something (such as Protocol SIFT) replaced the file.
    local merged
    merged=$(dfir-lab-configure-claude 2>&1 < /dev/null)
    local rc=$?
    log "dfir-lab-configure-claude (exit $rc):"
    printf '%s\n' "$merged" >> "$LOG"
    if [ "$rc" -ne 0 ]; then
        incomplete "the course settings could not be written into Claude Code's settings file." \
            "Tell the TA and send the log."
        return
    fi
    case "$merged" in
        *"Updated Claude Code settings"*) ok "added the course settings to ~/.claude/settings.json (kept Protocol SIFT's)"; changed ;;
        *) [ "$fresh" -eq 1 ] || done_already "the course settings are in ~/.claude/settings.json" ;;
    esac
    TOOLKIT_OK=1
}

# ---------------------------------------------------------------------------
# Step 6: case kit
# ---------------------------------------------------------------------------

kit_hash_accepted() {
    case " $R_CASE_KIT_SHA256 " in *" $1 "*) return 0 ;; esac
    return 1
}

# Sorts "hash  path" lines by path.
sort_sums() { awk '{ print substr($0, 67) "\t" $0 }' | LC_ALL=C sort | cut -f2-; }

# Writes MANIFEST.lab.sha256 in directory $1 from .dfir-lab/kit.sha256 and
# .dfir-lab/overlay.sha256 (overlay files replace kit files of the same name).
compose_manifest() {
    local d=$1
    { sudo awk 'NR == FNR { ov[substr($0, 67)] = 1; next } !(substr($0, 67) in ov)' \
          "$d/.dfir-lab/overlay.sha256" "$d/.dfir-lab/kit.sha256" &&
      sudo cat "$d/.dfir-lab/overlay.sha256"; } | sort_sums \
        | sudo tee "$d/MANIFEST.lab.sha256.new" > /dev/null &&
        sudo mv "$d/MANIFEST.lab.sha256.new" "$d/MANIFEST.lab.sha256"
}

# Root owns everything from the kit and the overlay (the student can read but
# not change it); the student owns the working folders and what is in them.
fix_case_permissions() {
    local d=$1 u g s
    u=$(id -un); g=$(id -gn)
    local prune=()
    for s in $STUDENT_DIRS; do prune+=(! -name "$s"); done
    run sudo find "$d" -mindepth 1 -maxdepth 1 "${prune[@]}" -exec chown -R root:root {} + || return 1
    run sudo find "$d" -mindepth 1 -maxdepth 1 "${prune[@]}" -exec chmod -R u+rwX,go+rX,go-w {} + || return 1
    run sudo chown root:root "$d" && run sudo chmod 755 "$d" || return 1
    for s in $STUDENT_DIRS; do
        sudo mkdir -p "$d/$s" && run sudo chown "$u:$g" "$d/$s" && run sudo chmod 755 "$d/$s" || return 1
    done
}

# Verifies an installed case folder. Returns 0 when it is complete and right.
case_verified() {
    [ -d "$CASE_DIR/.dfir-lab" ] || { log "case: no installed case folder"; return 1; }
    local zipsum
    zipsum=$(cut -c1-64 "$CASE_DIR/.dfir-lab/kit-zip.sha256" 2>/dev/null)
    kit_hash_accepted "$zipsum" || { log "case: installed kit $zipsum is not an accepted kit"; return 1; }
    if [ -n "$KIT_FOUND_SHA" ] && [ "$KIT_FOUND_SHA" != "$zipsum" ] && [ -n "$KIT_ARG" ]; then
        log "case: --kit names a different accepted kit"; return 1
    fi
    (cd "$CASE_DIR" && sha256sum -c --status --strict .dfir-lab/kit.sha256) 2>/dev/null \
        || { log "case: kit files changed or missing"; return 1; }
    if ! cmp -s "$CASE_DIR/.dfir-lab/overlay.sha256" "$PAYLOAD/overlay.sha256" || \
       ! (cd "$CASE_DIR" && sha256sum -c --status --strict .dfir-lab/overlay.sha256) 2>/dev/null; then
        log "case: overlay differs from this version"
        return 2
    fi
    (cd "$CASE_DIR" && sha256sum -c --status --strict MANIFEST.lab.sha256) 2>/dev/null \
        || { log "case: MANIFEST.lab.sha256 does not verify"; return 1; }
    # Nothing outside the working folders may be writable by the student.
    local w
    w=$(find "$CASE_DIR" \( -path "$CASE_DIR/analysis" -o -path "$CASE_DIR/reports" -o \
            -path "$CASE_DIR/exports" -o -path "$CASE_DIR/.claude" \) -prune -o -writable -print 2>/dev/null | head -n 3)
    [ -z "$w" ] || { log "case: writable by the student: $w"; return 1; }
    local s
    for s in $STUDENT_DIRS; do
        [ -d "$CASE_DIR/$s" ] && [ -w "$CASE_DIR/$s" ] || { log "case: $s missing or not writable"; return 1; }
    done
    return 0
}

update_overlay() {  # kit verified, overlay differs: replace the overlay files only
    local d=$CASE_DIR p
    log "updating the case overlay in $d"
    while read -r _ p; do
        case "$p" in ""|/*|*..*) continue ;; esac
        sudo rm -f -- "$d/$p"
    done < "$d/.dfir-lab/overlay.sha256"
    run sudo cp -R "$PAYLOAD/case-overlay/." "$d/" || return 1
    run sudo cp "$PAYLOAD/overlay.sha256" "$d/.dfir-lab/overlay.sha256" || return 1
    compose_manifest "$d" || return 1
    fix_case_permissions "$d" || return 1
}

KIT_FOUND=""
KIT_FOUND_SHA=""
KIT_REJECTED=()

find_kit() {
    local candidates=() f d sum newest=0 m
    if [ -n "$KIT_ARG" ]; then
        [ -f "$KIT_ARG" ] && candidates+=("$KIT_ARG")
    else
        local old
        old=$(shopt -p nullglob); shopt -s nullglob
        for d in "$HOME/Downloads" "$HOME/Desktop" "$HOME"; do
            for f in "$d"/$R_CASE_KIT_PATTERN; do [ -f "$f" ] && candidates+=("$f"); done
        done
        eval "$old"
    fi
    for f in "${candidates[@]}"; do
        out "      checking $f ..."
        sum=$(sha256sum -- "$f" | cut -c1-64)
        log "kit candidate $f sha256=$sum"
        if kit_hash_accepted "$sum"; then
            m=$(stat -c %Y -- "$f")
            if [ "$m" -ge "$newest" ]; then newest=$m; KIT_FOUND=$f; KIT_FOUND_SHA=$sum; fi
        else
            KIT_REJECTED+=("$f")
        fi
    done
}

validate_kit_zip() {  # $1 = zip; prints the top-level directory name
    local z=$1 list top
    list=$(unzip -Z1 "$z" 2>> "$LOG") || { log "kit: not a readable zip"; return 1; }
    if printf '%s\n' "$list" | grep -qE '(^/|(^|/)\.\.(/|$)|\\)'; then
        log "kit: unsafe path in zip"; return 1
    fi
    top=$(printf '%s\n' "$list" | cut -d/ -f1 | sort -u)
    if [ "$(printf '%s\n' "$top" | wc -l)" -ne 1 ] || ! printf '%s' "$top" | grep -qE '^[A-Za-z0-9._-]+$'; then
        log "kit: expected exactly one top-level directory, got: $top"; return 1
    fi
    local r
    for r in evidence/ derived/ README.md MANIFEST.sha256 tools.list; do
        printf '%s\n' "$list" | grep -qxF "$top/$r" || \
            { [ "${r%/}" != "$r" ] && printf '%s\n' "$list" | grep -q "^$top/$r"; } || \
            { log "kit: missing $top/$r"; return 1; }
    done
    if unzip -Z "$z" 2>/dev/null | grep -q '^l'; then log "kit: contains a symbolic link"; return 1; fi
    if printf '%s\n' "$list" | grep -q ' '; then log "kit: some paths contain spaces (EvtxECmd wrappers fail on them)"; fi
    printf '%s\n' "$top"
}

install_kit() {
    local zip="$WORK/kit.zip" top staging="/cases/.$R_CASE_ID.staging" unz="/cases/.$R_CASE_ID.unzip"
    ln -sf -- "$KIT_FOUND" "$zip"     # a path without spaces
    local zsize free_kb
    zsize=$(stat -L -c %s -- "$zip")
    free_kb=$(df -Pk /cases 2>/dev/null | awk 'NR==2 {print $4}')
    if [ -n "$free_kb" ] && [ $((free_kb * 1024)) -lt $((zsize * 2 + 1073741824)) ]; then
        incomplete "there is not enough free disk space to unpack the case kit." \
            "Delete large files you do not need, then run the same setup command again."
        return 1
    fi
    top=$(validate_kit_zip "$zip") || {
        incomplete "the case kit file $KIT_FOUND is not laid out as expected." \
            "Download it again from the course site; if it happens again, tell the TA."
        return 1; }
    sudo mkdir -p /cases
    sudo rm -rf "$staging" "$unz"
    out "      unpacking the case kit..."
    if ! run sudo unzip -q "$zip" -d "$unz" || ! sudo mv "$unz/$top" "$staging"; then
        incomplete "the case kit could not be unpacked." \
            "Download it again from the course site, then run the same setup command again."
        return 1
    fi
    sudo rm -rf "$unz"
    # Readable by everyone, writable by nobody but root, whatever the zip said.
    run sudo chown -R root:root "$staging" && run sudo chmod -R u+rwX,go+rX,go-w "$staging" || return 1
    if ! (cd "$staging" && run sudo sha256sum -c --quiet --strict MANIFEST.sha256); then
        incomplete "the case kit's own checksums do not match: the download is damaged." \
            "Delete $KIT_FOUND, download it again from the course site, then run the same setup command again."
        return 1
    fi
    # Remember every kit file (the zip's hash was accepted, so these are authentic).
    sudo mkdir -p "$staging/.dfir-lab"
    sudo sh -c 'cd "$1" && find . -type f ! -path "./.dfir-lab/*" -printf "%P\n" | LC_ALL=C sort \
        | xargs -r -d "\n" sha256sum -- > .dfir-lab/kit.sha256' sh "$staging" || return 1
    printf '%s  %s\n' "$KIT_FOUND_SHA" "$(basename -- "$KIT_FOUND")" | sudo tee "$staging/.dfir-lab/kit-zip.sha256" > /dev/null
    # The case overlay from this script goes on top.
    run sudo cp -R "$PAYLOAD/case-overlay/." "$staging/" || return 1
    run sudo cp "$PAYLOAD/overlay.sha256" "$staging/.dfir-lab/overlay.sha256" || return 1
    compose_manifest "$staging" || return 1
    # Keep the student's work from an earlier installation.
    local s
    if [ -d "$CASE_DIR/.dfir-lab" ]; then
        for s in $STUDENT_DIRS; do
            if [ -e "$CASE_DIR/$s" ]; then sudo rm -rf "$staging/$s"; run sudo mv "$CASE_DIR/$s" "$staging/$s"; fi
        done
    fi
    fix_case_permissions "$staging" || return 1
    if [ -e "$CASE_DIR" ] || [ -L "$CASE_DIR" ]; then
        if [ -d "$CASE_DIR/.dfir-lab" ]; then
            run sudo mv "$CASE_DIR" "/cases/.$R_CASE_ID.old" || return 1
        else
            local aside
            aside="/cases/$R_CASE_ID.before-setup-$(date +%Y%m%d%H%M%S)"
            run sudo mv "$CASE_DIR" "$aside" || return 1
            NOTES+=("A folder $CASE_DIR that the setup did not create was moved to $aside.")
        fi
    fi
    run sudo mv "$staging" "$CASE_DIR" || return 1
    sudo rm -rf "/cases/.$R_CASE_ID.old"
    return 0
}

case_kit() {
    step 6 "Installing the case files (case $R_CASE_ID: $R_CASE_TITLE)"
    local nfiles
    if [ -n "$KIT_ARG" ] && [ ! -f "$KIT_ARG" ]; then
        incomplete "the case kit file $KIT_ARG does not exist." \
            "Check the file name after --kit, or leave --kit out to search ~/Downloads."
        return
    fi
    [ -n "$KIT_ARG" ] && find_kit
    case_verified
    local v=$?
    if [ "$v" -eq 2 ]; then
        if update_overlay && case_verified; then
            ok "updated the case instructions and checkpoints in $CASE_DIR"
            changed; v=0
        else
            v=1
        fi
    elif [ "$v" -eq 0 ]; then
        nfiles=$(wc -l < "$CASE_DIR/MANIFEST.lab.sha256")
        done_already "case $R_CASE_ID is installed in $CASE_DIR ($nfiles files verified)"
    fi
    if [ "$v" -eq 0 ]; then CASE_OK=1; return; fi

    [ -z "$KIT_ARG" ] && find_kit
    if [ -z "$KIT_FOUND" ]; then
        if [ ${#KIT_REJECTED[@]} -gt 0 ]; then
            incomplete "the case kit file ${KIT_REJECTED[0]} is damaged or not the current version." \
                "Delete it, download $R_CASE_KIT_PATTERN again from the course site inside this VM, then run the same setup command again."
        else
            incomplete "the case kit is not on this VM yet." \
                "Inside this VM, download the case kit ($R_CASE_KIT_PATTERN) from the course site; it goes to ~/Downloads. Then run the same setup command again."
        fi
        return
    fi
    if ! install_kit; then
        [ ${#INCOMPLETE[@]} -gt 0 ] || incomplete "the case files could not be installed." \
            "Run the same setup command again; if it happens again, tell the TA and send the log."
        return
    fi
    if case_verified; then
        nfiles=$(wc -l < "$CASE_DIR/MANIFEST.lab.sha256")
        ok "case $R_CASE_ID installed in $CASE_DIR ($nfiles files verified; evidence is read-only)"
        CASE_OK=1
        changed
    else
        incomplete "the installed case files did not verify." \
            "Run the same setup command again; if it happens again, tell the TA and send the log."
    fi
}

# ---------------------------------------------------------------------------
# Step 7: desktop comfort
# ---------------------------------------------------------------------------

gio_attr() {  # $1 file, $2 attribute -> value
    gio info -a "$2" -- "$1" 2>/dev/null | awk -v a="  $2: " 'index($0, a) == 1 { print substr($0, length(a) + 1) }'
}

shortcut_ready() {  # what DING (GNOME 46 desktop icons) needs to launch a .desktop file
    local f=$1 mode
    [ "$(gio_attr "$f" standard::content-type)" = "application/x-desktop" ] || return 1
    [ "$(gio_attr "$f" access::can-execute)" = "TRUE" ] || return 1
    [ "$(gio_attr "$f" metadata::trusted)" = "true" ] || return 1
    mode=$(stat -c %a -- "$f")
    [ $((8#$mode & 8#002)) -eq 0 ] || return 1
    return 0
}

explicit_default() {  # $1 = MIME type -> desktop file named in ~/.config/mimeapps.list
    awk -F= -v t="$1" '/^\[/ { s = ($0 == "[Default Applications]") }
        s && $1 == t { v = $2; sub(/;.*$/, "", v); print v; exit }' \
        "${XDG_CONFIG_HOME:-$HOME/.config}/mimeapps.list" 2>/dev/null
}

bashrc_block() {
    cat <<EOF
$BASHRC_BEGIN
# Added by the lab setup script ("bash bootstrap.sh --uninstall" removes it).
# SIFT turns on noclobber earlier in this file, which makes "command > file"
# refuse to overwrite an existing file. The lab expects overwriting to work.
set +o noclobber
# Claude Code is installed in ~/.local/bin.
case ":\$PATH:" in *":\$HOME/.local/bin:"*) ;; *) PATH="\$HOME/.local/bin:\$PATH" ;; esac
$BASHRC_END
EOF
}

remove_bashrc_block() {
    local rc="$HOME/.bashrc" tmp
    [ -f "$rc" ] && grep -qxF "$BASHRC_BEGIN" "$rc" || return 1
    tmp=$(mktemp)
    awk -v b="$BASHRC_BEGIN" -v e="$BASHRC_END" '$0 == b { skip = 1 } !skip { print } $0 == e { skip = 0 }' "$rc" > "$tmp" \
        && cat "$tmp" > "$rc"
    rm -f "$tmp"
    # Drop the blank line the setup added before the block.
    if [ "$(tail -c 2 "$rc" | od -An -c | tr -d ' ')" = '\n\n' ]; then
        tmp=$(mktemp); head -c -1 "$rc" > "$tmp" && cat "$tmp" > "$rc"; rm -f "$tmp"
    fi
    return 0
}

desktop() {
    step 7 "Adjusting the desktop for the lab"
    if ! session_env; then
        warn "no desktop session was found, so the desktop settings were skipped. Run the setup command again from a terminal on the VM desktop."
        return
    fi
    local before=$CHANGED

    # a) Screen lock and blanking.
    local delay lock
    delay=$(gsettings get org.gnome.desktop.session idle-delay 2>/dev/null)
    lock=$(gsettings get org.gnome.desktop.screensaver lock-enabled 2>/dev/null)
    log "idle-delay=$delay lock-enabled=$lock"
    if [ "$delay" = "uint32 0" ] && [ "$lock" = "false" ]; then
        done_already "the screen does not lock or go blank"
    elif run gsettings set org.gnome.desktop.session idle-delay 0 && \
         run gsettings set org.gnome.desktop.screensaver lock-enabled false && \
         [ "$(gsettings get org.gnome.desktop.session idle-delay)" = "uint32 0" ] && \
         [ "$(gsettings get org.gnome.desktop.screensaver lock-enabled)" = "false" ]; then
        run gsettings set org.gnome.desktop.screensaver ubuntu-lock-on-suspend false || true
        ok "turned off the screen lock and screen blanking"
        changed
    else
        warn "could not turn off the screen lock (the screen locks after 5 idle minutes; the password is forensics)"
    fi

    # b) Chromium as the browser for web links (Firefox on this VM is a broken
    #    stub). Chromium is already the implicit choice while it is the only
    #    registered handler; write it as the explicit default in
    #    ~/.config/mimeapps.list so that stays true.
    if [ ! -f "/var/lib/snapd/desktop/applications/$BROWSER_DESKTOP" ]; then
        warn "Chromium was not found, so the default browser was not changed"
    else
        local t missing=0
        for t in x-scheme-handler/http x-scheme-handler/https text/html; do
            [ "$(explicit_default "$t")" = "$BROWSER_DESKTOP" ] || missing=1
        done
        log "explicit defaults: http=$(explicit_default x-scheme-handler/http) https=$(explicit_default x-scheme-handler/https) html=$(explicit_default text/html); xdg-settings=$(xdg-settings get default-web-browser 2>/dev/null)"
        if [ "$missing" -eq 0 ]; then
            done_already "Chromium opens web links"
        else
            run xdg-settings set default-web-browser "$BROWSER_DESKTOP"
            run xdg-mime default "$BROWSER_DESKTOP" x-scheme-handler/http x-scheme-handler/https text/html
            if [ "$(explicit_default x-scheme-handler/https)" = "$BROWSER_DESKTOP" ] && \
               [ "$(explicit_default x-scheme-handler/http)" = "$BROWSER_DESKTOP" ] && \
               gio mime x-scheme-handler/https 2>/dev/null | grep -q "^Default application.*: $BROWSER_DESKTOP\$"; then
                ok "Chromium now opens web links"
                changed
            else
                warn "could not make Chromium the default browser; open links by copying them into Chromium"
            fi
        fi
    fi

    # c) ~/.bashrc: noclobber off, ~/.local/bin on PATH.
    local rc="$HOME/.bashrc" want have
    want=$(bashrc_block)
    if grep -qxF "$BASHRC_BEGIN" "$rc" 2>/dev/null; then
        have=$(awk -v b="$BASHRC_BEGIN" -v e="$BASHRC_END" '$0 == b { p = 1 } p { print } $0 == e { p = 0 }' "$rc")
        if [ "$have" = "$want" ]; then
            done_already "new terminals let '>' overwrite files (noclobber off)"
        else
            remove_bashrc_block
            printf '\n%s\n' "$want" >> "$rc"
            ok "updated the lab block in ~/.bashrc"
            changed
        fi
    elif printf '\n%s\n' "$want" >> "$rc"; then
        ok "new terminals let '>' overwrite files (turned noclobber off in ~/.bashrc)"
        changed
    else
        warn "could not change ~/.bashrc; in a terminal, '>' will not overwrite existing files (use '>|')"
    fi

    # d) Desktop shortcuts: DING launches a .desktop file only when it is a
    #    valid desktop file, executable, not writable by others, in a Desktop
    #    folder not writable by others, and has metadata::trusted = true.
    local s f bad=() fixed=0 dmode
    dmode=$(stat -c %a -- "$HOME/Desktop" 2>/dev/null || echo 0)
    if [ $((8#$dmode & 8#002)) -ne 0 ]; then run chmod o-w "$HOME/Desktop"; fi
    for s in $SHORTCUTS; do
        f="$HOME/Desktop/$s.desktop"
        [ -f "$f" ] || { bad+=("$s"); continue; }
        shortcut_ready "$f" && continue
        run chmod 755 "$f"
        run gio set -- "$f" metadata::trusted true
        if shortcut_ready "$f"; then fixed=$((fixed + 1)); else bad+=("$s"); fi
    done
    for s in $SHORTCUTS; do
        f="$HOME/Desktop/$s.desktop"
        [ -f "$f" ] && log "$s: type=$(gio_attr "$f" standard::content-type) exec=$(gio_attr "$f" access::can-execute) trusted=$(gio_attr "$f" metadata::trusted) mode=$(stat -c %a "$f")"
    done
    if [ ${#bad[@]} -gt 0 ]; then
        warn "could not mark these desktop shortcuts as trusted: ${bad[*]}. Right-click each one and choose 'Allow Launching'."
    elif [ "$fixed" -gt 0 ]; then
        ok "marked the lab's desktop shortcuts as trusted (double-click runs them)"
        changed
    else
        done_already "the lab's desktop shortcuts are trusted (double-click runs them)"
    fi
    [ "$CHANGED" -eq "$before" ] || log "desktop settings changed"
}

# ---------------------------------------------------------------------------
# Step 8: readiness check and summary
# ---------------------------------------------------------------------------

finish_checks() {
    step 8 "Checking the result"
    if [ "$TOOLKIT_OK" -ne 1 ] || ! command -v dfir-lab > /dev/null 2>&1; then
        out "      skipped: the lab toolkit is not installed"
        return
    fi
    local json rc
    json=$(dfir-lab check --quick --json --no-gui 2>> "$LOG" < /dev/null)
    rc=$?
    log "dfir-lab check --quick --json (exit $rc):"
    printf '%s\n' "$json" >> "$LOG"
    if ! command -v jq > /dev/null 2>&1 || ! printf '%s' "$json" | jq -e '.results' > /dev/null 2>&1; then
        if dfir-lab check --only 1,2,3 --no-gui >> "$LOG" 2>&1 < /dev/null; then
            out "      passed: system, tools and case files"
        else
            incomplete "the readiness check found a problem with the system, the tools or the case files." \
                "Run  dfir-lab check  for details, or tell the TA and send the log."
        fi
        return
    fi
    local id status result todo name
    for id in 1 2 3 4 5; do
        status=$(printf '%s' "$json" | jq -r ".results[] | select(.id == $id) | .status")
        result=$(printf '%s' "$json" | jq -r ".results[] | select(.id == $id) | .result")
        todo=$(printf '%s' "$json" | jq -r ".results[] | select(.id == $id) | .todo")
        name=$(printf '%s' "$json" | jq -r ".results[] | select(.id == $id) | .name")
        [ -n "$status" ] || continue
        if [ "$CASE_OK" -ne 1 ] && { [ "$id" = 2 ] || [ "$id" = 3 ]; } && [ "$status" != "PASS" ]; then
            # Follows from the missing case files, which are already listed.
            out "      waiting: $name (needs the case files)"
            continue
        fi
        case "$id:$status" in
            [123]:PASS) out "      passed: $name: $result" ;;
            [123]:WARN) out "      warning: $name: $result"; WARNINGS+=("$name: $result") ;;
            [123]:SKIP) out "      skipped: $name: $result" ;;
            [123]:*) incomplete "readiness check '$name' failed: $result" "${todo:-Tell the TA and send the log.}" ;;
            4:PASS) out "      passed: Duke AI key: stored" ;;
            4:*) out "      not yet: no Duke AI key is stored (that is your next step)" ;;
            5:PASS) out "      passed: Duke AI gateway: reachable" ;;
            5:*) out "      not now: the Duke AI gateway cannot be reached from this VM"
                 NOTES+=("The Duke AI gateway was not reachable during setup. Before the lab, connect your computer to the Duke VPN (or the Duke network); \"Check my setup\" tests it.") ;;
        esac
    done
}

summary() {
    end_step
    local line="================================================================"
    out ""
    out "$line"
    if [ ${#INCOMPLETE[@]} -eq 0 ]; then
        out " SETUP COMPLETE"
    else
        out " SETUP INCOMPLETE: run the same command again after doing what is listed below"
    fi
    out "$line"
    out " Installed:"
    local psift="not installed" tk="not installed" cs="not installed" cc="not installed"
    [ "$CLAUDE_OK" -eq 1 ] && cc="$CLAUDE_FOUND   ($CLAUDE_BIN)"
    [ "$PSIFT_OK" -eq 1 ] && psift="${R_PROTOCOL_SIFT_COMMIT:0:7}   (~/.claude)"
    [ "$TOOLKIT_OK" -eq 1 ] && tk="$R_VERSION   (dfir-lab, desktop shortcuts)"
    [ "$CASE_OK" -eq 1 ] && cs="$R_CASE_ID   ($CASE_DIR)"
    out "   Claude Code     $cc"
    out "   Protocol SIFT   $psift"
    out "   Lab toolkit     $tk"
    out "   Case files      $cs"
    if [ ${#INCOMPLETE[@]} -gt 0 ]; then
        out ""
        out " Not finished:"
        local item
        for item in "${INCOMPLETE[@]}"; do
            out "   - ${item%%|*}"
            out "     What to do: ${item#*|}"
        done
        out ""
        out " Running the command again is safe: it skips everything that is already done."
    fi
    if [ ${#WARNINGS[@]} -gt 0 ]; then
        out ""
        out " Warnings (the lab still works):"
        local w
        for w in "${WARNINGS[@]}"; do out "   - $w"; done
    fi
    if [ ${#NOTES[@]} -gt 0 ]; then
        out ""
        local n
        for n in "${NOTES[@]}"; do out " Note: $n"; done
    fi
    out ""
    if [ "$CHANGED" -eq 0 ] && [ ${#INCOMPLETE[@]} -eq 0 ]; then
        out " This run changed nothing: everything was already set up."
    elif [ "$CHANGED" -eq 0 ]; then
        out " This run changed nothing."
    fi
    out " Log: $LOG"
    if [ ${#INCOMPLETE[@]} -eq 0 ]; then
        out ""
        out " Next steps:"
        out "   1. Double-click \"Set up Duke AI access\" on the desktop and paste your Duke AI key."
        out "   2. Double-click \"Check my setup\"."
    fi
    out "$line"
    log "finished: changed=$CHANGED incomplete=${#INCOMPLETE[@]} warnings=${#WARNINGS[@]} seconds=$SECONDS"
}

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

uninstall() {
    out "CYBERSEC 510 DFIR lab setup $R_VERSION: removing the lab setup$([ "$PURGE" -eq 1 ] && echo ' (purge)')"
    out "Log: $LOG"
    if [ "$PURGE" -eq 1 ] && [ "$YES" -ne 1 ]; then
        out ""
        out "--purge also deletes Claude Code, ~/.claude (Protocol SIFT and your Claude Code"
        out "sessions), your stored Duke AI key, ~/dfir-lab and your work in $CASE_DIR."
        if [ -t 0 ]; then
            local ans=""
            read -r -p "Type yes to delete all of that: " ans
            [ "$ans" = "yes" ] || { out "Stopped. Nothing was changed."; exit 2; }
        else
            out "Stopped. Nothing was changed. (Add --yes to confirm.)"; exit 2
        fi
    fi
    sudo -n true 2>/dev/null || sudo -v || die "administrator rights are needed."
    local args=(--user "$(id -un)" --uninstall)
    [ "$PURGE" -eq 1 ] && args+=(--purge)
    if run sudo sh "$PAYLOAD/lab/install.sh" "${args[@]}"; then
        out "  removed the lab toolkit (dfir-lab, desktop shortcuts, /etc/dfir-lab); Claude Code settings restored"
    else
        out "  WARNING: the toolkit uninstaller reported a problem (see the log)"
    fi
    sudo rm -f "$TOOLKIT_STAMP"
    sudo rmdir /usr/local/lib/dfir-lab 2>/dev/null || true
    if [ -d "$CASE_DIR" ]; then
        if [ "$PURGE" -eq 1 ]; then
            run sudo rm -rf "$CASE_DIR"
            out "  removed $CASE_DIR"
        else
            local item keep=0
            for item in "$CASE_DIR"/* "$CASE_DIR"/.[!.]*; do
                [ -e "$item" ] || continue
                case " $STUDENT_DIRS " in *" $(basename -- "$item") "*)
                    if [ -n "$(ls -A -- "$item" 2>/dev/null)" ]; then keep=1; continue; fi ;;
                esac
                sudo rm -rf -- "$item"
            done
            if [ "$keep" -eq 1 ]; then
                sudo chown "$(id -un):$(id -gn)" "$CASE_DIR"
                out "  removed the case files from $CASE_DIR; kept your work there"
            else
                sudo rmdir "$CASE_DIR" 2>/dev/null
                out "  removed $CASE_DIR"
            fi
        fi
    fi
    if remove_bashrc_block; then out "  removed the lab block from ~/.bashrc"; fi
    if [ "$PURGE" -eq 1 ]; then
        rm -rf "$HOME/.local/bin/claude" "$HOME/.local/share/claude" "$HOME/.cache/claude" \
               "$HOME/.claude" "$HOME/.claude.json" "$HOME/.claude.json.backup"
        out "  removed Claude Code, ~/.claude (Protocol SIFT), your Duke AI key and ~/dfir-lab"
    else
        out "  kept: Claude Code, Protocol SIFT (~/.claude), your Duke AI key and your work"
    fi
    out "  not undone: the open-vm-tools-desktop package, the screen-lock and default-browser settings"
    out "Done."
    log "uninstall finished"
}

# ---------------------------------------------------------------------------

main() {
    parse_args "$@"
    refuse_root
    SELF=$(readlink -f -- "${BASH_SOURCE[0]}" 2>/dev/null)
    if [ -z "$SELF" ] || [ ! -f "$SELF" ]; then
        echo "Save bootstrap.sh to a file and run it with: bash bootstrap.sh"
        exit 2
    fi
    start_log
    take_lock
    trap cleanup EXIT
    trap on_interrupt INT TERM
    extract_payload
    if [ "$MODE" = "uninstall" ]; then
        uninstall
        exit 0
    fi
    out "CYBERSEC 510 DFIR lab setup (version $R_VERSION)"
    out "This prepares this SIFT virtual machine for the lab. It needs internet access"
    out "and usually takes a few minutes. It is safe to run again: finished steps are skipped."
    out "Full log: $LOG"
    out ""
    preflight
    packages
    claude_code
    protocol_sift
    toolkit
    case_kit
    desktop
    finish_checks
    summary
    [ ${#INCOMPLETE[@]} -eq 0 ] && exit 0
    exit 1
}

main "$@"
exit 3
__DFIR_LAB_PAYLOAD_BELOW__
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
uuiiiy666KKLLrroosuDlv8DryL1bQDAAwA=
