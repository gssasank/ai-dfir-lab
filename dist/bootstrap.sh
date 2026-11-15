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
R_VERSION='0.4.0'
R_CLAUDE_CODE_VERSION='2.1.322'
R_PROTOCOL_SIFT_REPO='https://github.com/teamdfir/protocol-sift.git'
R_PROTOCOL_SIFT_COMMIT='40bed7a96bfd986ea048c3b2aeb9d788b2f3400c'
R_GATEWAY_BASE_URL='https://litellm.oit.duke.edu'
R_CASE_ID='szechuan'
R_CASE_TITLE='Szechuan Sauce'
R_CASE_KIT_PATTERN='szechuan-kit-*.zip'
R_CASE_KIT_SHA256='86a32199fc9d506afb785f4f6705be84484f510027dc12d79533fbd1fa4107ed b7157b32995e8e323e7d2d3bf8f214cae1e029c6a6ae5ca8981319194051d42a'
R_PAYLOAD_SHA256='879936f95c78eda27274f84255ec9fcdfd67e6303120766a71c1ce8c1bf9e749'
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
