#!/usr/bin/env bash
# CYBERSEC 510 DFIR lab setup script.
#
# Turns an untouched SANS SIFT Workstation VM into a ready lab machine:
# Claude Code (pinned version), Protocol SIFT, the dfir-lab toolkit, the case
# files, the domain controller's disk image (unpacked, read-only), and a few
# desktop settings. Safe to run any number of times.
#
#   bash bootstrap.sh [--yes] [--kit PATH] [--image PATH]
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
R_VERSION='1.1.0'
R_CLAUDE_CODE_VERSION='2.1.322'
R_PROTOCOL_SIFT_REPO='https://github.com/teamdfir/protocol-sift.git'
R_PROTOCOL_SIFT_COMMIT='40bed7a96bfd986ea048c3b2aeb9d788b2f3400c'
R_GATEWAY_BASE_URL='https://litellm.oit.duke.edu'
R_CASE_ID='szechuan'
R_CASE_TITLE='Szechuan Sauce'
R_CASE_KIT_PATTERN='szechuan-kit-*.zip'
R_CASE_KIT_SHA256='86a32199fc9d506afb785f4f6705be84484f510027dc12d79533fbd1fa4107ed'
R_CASE_KIT_URL='https://raw.githubusercontent.com/gssasank/ai-dfir-lab/v1.1.0/kit/szechuan-kit-0.1.0.zip'
R_IMAGE_ZIP_PATTERN='DC01-E01*.zip'
R_IMAGE_ZIP_NAME='DC01-E01.zip'
R_IMAGE_ZIP_SIZE='4836649413'
R_IMAGE_ZIP_SHA256='efe06d12388dbc000fa4ae306746ddaca3893a6cdbd55311b52f5833e717acd9'
R_IMAGE_ZIP_DIR='E01-DC01'
R_IMAGE_FILES='20200918_0347_CDrive.E01:c0c8fe58dcdb65f6574cb63b34f086856087cfaf06872ed702a4c0c264446bac 20200918_0347_CDrive.E02:73f87df9de0fa9ed41c6fdc360f0e15c3d13955e7fd24f5e59a5b2e282d6b384 20200918_0347_CDrive.E01.txt:af08169bc8c32b27fbaa85f26646f8c1bb9f5f71f0ee04a759f9064928c8ca79'
R_IMAGE_UNPACKED_BYTES='4872432596'
R_IMAGE_SHORT_NAME='DC01'
R_IMAGE_MOUNT_POINT='/mnt/dc01'
R_PAYLOAD_SHA256='c7975a4645428ec0a8f763e436b1285deba449bd21ae81d708d715ad787c5221'
# -----------------------------------------------------------------------------

PAYLOAD_MARKER="__DFIR_LAB_PAYLOAD""_BELOW__"
CASE_DIR="/cases/$R_CASE_ID"
DISK_DIR="$CASE_DIR/disk"
DISK_STAGING="/cases/.$R_CASE_ID-disk.staging"
UPDATE_AUTOSTART="$HOME/.config/autostart/update-notifier.desktop"
RELEASE_UPGRADES="/etc/update-manager/release-upgrades"
STATE_DIR="$HOME/dfir-lab"
LOG_DIR="$STATE_DIR/logs"
CLAUDE_BIN="$HOME/.local/bin/claude"
PSIFT_MARKER="$HOME/.claude/.protocol-sift-install"
TOOLKIT_STAMP="/usr/local/lib/dfir-lab/.setup-stamp"
SHORTCUTS="dfir-lab-setup-key dfir-lab-start dfir-lab-check dfir-lab-replay dfir-lab-use-subscription"
STUDENT_DIRS="analysis reports exports .claude"
BROWSER_DESKTOP="chromium_chromium.desktop"
BASHRC_BEGIN="# >>> dfir-lab setup (CYBERSEC 510) >>>"
BASHRC_END="# <<< dfir-lab setup (CYBERSEC 510) <<<"
TOTAL_STEPS=9
MIN_FREE_GB=3

SELF=""
LOG=""
WORK=""
PAYLOAD=""
YES=0
KIT_ARG=""
IMAGE_ARG=""
MODE="setup"
PURGE=0
CHANGED=0
CLAUDE_OK=0
PSIFT_OK=0
TOOLKIT_OK=0
CASE_OK=0
IMAGE_OK=0
CLAUDE_FOUND=""
SETUP_ROUTE="duke"
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
        "/cases/.$R_CASE_ID.old" "$DISK_STAGING" 2>/dev/null || true
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

  bash bootstrap.sh [--yes] [--kit PATH] [--image PATH]
  bash bootstrap.sh --uninstall [--purge] [--yes]

  --kit PATH    use this case kit zip (default: an accepted $R_CASE_KIT_PATTERN
                in ~/Downloads, ~/Desktop or your home folder, else download it)
  --image PATH  use this disk image zip (default: look for $R_IMAGE_ZIP_PATTERN
                in the same places)
  --yes         answer yes to questions (for automation); keeps the disk
                image zip instead of offering to delete it
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
            --image) [ $# -ge 2 ] || { echo "--image needs a file name" >&2; exit 2; }
                     IMAGE_ARG=$2; shift 2 ;;
            --image=*) IMAGE_ARG=${1#--image=}; shift ;;
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
    if [ -n "$IMAGE_ARG" ]; then
        case "$IMAGE_ARG" in /*) ;; *) IMAGE_ARG="$PWD/$IMAGE_ARG" ;; esac
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
    for h in claude.ai downloads.claude.ai github.com raw.githubusercontent.com; do
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

update_autostart_text() {
    cat <<'EOF'
[Desktop Entry]
Type=Application
Name=Update Notifier
Exec=update-notifier
Hidden=true
X-GNOME-Autostart-enabled=false
# Added by the CYBERSEC 510 lab setup script: no Software Updater pop-ups during the
# lab. "bash bootstrap.sh --uninstall" removes this file.
EOF
}

# Ubuntu's Software Updater pops up a few minutes after the package lists are
# refreshed (update-notifier launches update-manager), and a "new Ubuntu release"
# dialog appears too. Neither installs anything by itself, but both distract and
# invite a 9 GB upgrade in the middle of the lab. Nothing is upgraded here.
quiet_updates() {
    local did=0 uid
    uid=$(id -u)
    # The release-upgrade prompt: Prompt=never is the setting documented in the
    # file; check-new-release-gtk then does nothing.
    if [ -f "$RELEASE_UPGRADES" ] && ! grep -qx 'Prompt=never' "$RELEASE_UPGRADES"; then
        if run sudo sed -i 's/^Prompt=.*/Prompt=never/' "$RELEASE_UPGRADES" && \
           grep -qx 'Prompt=never' "$RELEASE_UPGRADES"; then
            did=1
        else
            warn "could not turn off the new-Ubuntu-release prompt; if a window offers an upgrade, close it"
        fi
    fi
    # update-notifier is what opens the Software Updater; keep it from starting
    # at login (a user autostart entry with the same name and Hidden=true) ...
    if [ "$(cat "$UPDATE_AUTOSTART" 2>/dev/null)" != "$(update_autostart_text)" ]; then
        mkdir -p "$(dirname "$UPDATE_AUTOSTART")" && update_autostart_text > "$UPDATE_AUTOSTART" && did=1
    fi
    # ... and stop the copy that is running now, with any updater window it opened.
    if pgrep -u "$uid" -x update-notifier > /dev/null 2>&1 || \
       pgrep -u "$uid" -f '^/usr/bin/python3 /usr/(bin/update-manager|lib/ubuntu-release-upgrader/check-new-release-gtk)' > /dev/null 2>&1; then
        pkill -u "$uid" -x update-notifier 2>/dev/null
        pkill -u "$uid" -f '^/usr/bin/python3 /usr/(bin/update-manager|lib/ubuntu-release-upgrader/check-new-release-gtk)' 2>/dev/null
        did=1
    fi
    log "release-upgrades: $(grep '^Prompt=' "$RELEASE_UPGRADES" 2>/dev/null); autostart override: $([ -f "$UPDATE_AUTOSTART" ] && echo present)"
    if [ "$did" -eq 1 ]; then
        ok "turned off the Software Updater pop-ups and the new-release prompt (nothing is upgraded)"
        changed
    else
        done_already "the Software Updater pop-ups and the new-release prompt are off"
    fi
}

packages() {
    step 2 "Installing missing system packages (copy and paste support)"
    quiet_updates
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
    for s in $STUDENT_DIRS disk; do prune+=(! -name "$s"); done
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
            -path "$CASE_DIR/exports" -o -path "$CASE_DIR/.claude" -o -path "$CASE_DIR/disk" \) -prune -o -writable -print 2>/dev/null | head -n 3)
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
        for s in $STUDENT_DIRS disk; do
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

# Downloads the kit from the release this script belongs to (R_CASE_KIT_URL)
# and accepts it only if its SHA-256 is in R_CASE_KIT_SHA256. An unverified kit
# is never installed. DFIR_LAB_KIT_URL replaces the URL for tests only (the
# repository is private until release, so the real URL answers 404 before then).
download_kit() {
    local url="${DFIR_LAB_KIT_URL:-$R_CASE_KIT_URL}" name dest code sum
    name=$(basename -- "$R_CASE_KIT_URL")
    dest="$WORK/download/$name"
    mkdir -p "$WORK/download"
    [ "$url" = "$R_CASE_KIT_URL" ] || log "kit URL overridden for testing by DFIR_LAB_KIT_URL"
    out "      downloading the case kit ($name, about 8 MB)..."
    code=$(curl -sSL --retry 3 --connect-timeout 15 -m 300 -o "$dest" -w '%{http_code}' "$url" 2>> "$LOG")
    local rc=$?
    log "kit download $url -> curl exit $rc, HTTP ${code:-000}"
    if [ "$rc" -ne 0 ] || [ "${code:-000}" != "200" ]; then
        rm -f "$dest"
        if [ "$rc" -ne 0 ] && [ "${code:-000}" = "000" ]; then
            incomplete "the case kit could not be downloaded (no connection to $(printf '%s' "$url" | cut -d/ -f3))." \
                "Check that the VM has internet, then run the same setup command again."
        else
            incomplete "the case kit could not be downloaded (the server answered HTTP ${code:-000})." \
                "Run the same setup command again in a few minutes; if it happens again, tell the TA and send the log."
        fi
        return 1
    fi
    sum=$(sha256sum -- "$dest" | cut -c1-64)
    log "downloaded kit sha256=$sum ($(stat -c %s -- "$dest") bytes)"
    if ! kit_hash_accepted "$sum"; then
        rm -f "$dest"
        incomplete "the downloaded case kit does not match its expected SHA-256, so it was not installed." \
            "Run the same setup command again; if it happens again, tell the TA and send the log."
        return 1
    fi
    ok "downloaded the case kit and verified its SHA-256"
    KIT_FOUND=$dest
    KIT_FOUND_SHA=$sum
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
    if [ -z "$KIT_FOUND" ] && [ -n "$KIT_ARG" ]; then
        incomplete "the case kit file $KIT_ARG is damaged or not the current version." \
            "Leave --kit out: the setup then downloads the kit itself. Run the same setup command again."
        return
    fi
    # No accepted kit on this VM: download it from the same release as this script.
    [ -z "$KIT_FOUND" ] && download_kit
    [ -n "$KIT_FOUND" ] || return
    case "$KIT_FOUND" in "$WORK"/*) [ ${#KIT_REJECTED[@]} -eq 0 ] ||
        note "ignored ${KIT_REJECTED[0]}: it is not the current case kit (you can delete it)" ;; esac
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
# Step 7: disk image
# ---------------------------------------------------------------------------

IMAGE_FOUND=""
IMAGE_REJECTED=()
IMAGE_KEPT_NOTE=""

gb() { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1073741824 }'; }

# "hash  name" lines for every image file, for sha256sum -c.
image_sums() {
    local item
    for item in $R_IMAGE_FILES; do printf '%s  %s\n' "${item#*:}" "${item%%:*}"; done
}

# The short links: DC01.E01 -> <long name>.E01, DC01.E02 -> <long name>.E02, ...
image_links() {
    local item name
    for item in $R_IMAGE_FILES; do
        name=${item%%:*}
        case "$name" in *.E[0-9][0-9]) printf '%s %s\n' "$R_IMAGE_SHORT_NAME.${name##*.}" "$name" ;; esac
    done
}

# Checks an unpacked image folder ($1): every file present, root-owned, mode
# 444, matching its SHA-256; the short links in place; the folder root-owned
# and not writable by anyone else.
image_folder_ok() {
    local d=$1 item name link target
    [ -d "$d" ] && [ "$(stat -c '%U %a' -- "$d")" = "root 755" ] || { log "image: folder $d missing or wrong mode"; return 1; }
    for item in $R_IMAGE_FILES; do
        name=${item%%:*}
        [ -f "$d/$name" ] && [ ! -L "$d/$name" ] || { log "image: $name missing"; return 1; }
        [ "$(stat -c '%U %a' -- "$d/$name")" = "root 444" ] || { log "image: $name not root-owned read-only"; return 1; }
    done
    while read -r link target; do
        [ "$(readlink -- "$d/$link" 2>/dev/null)" = "$target" ] || { log "image: link $link missing"; return 1; }
    done < <(image_links)
    (cd "$d" && image_sums | sha256sum -c --status --strict) 2>/dev/null || { log "image: checksums differ"; return 1; }
    return 0
}

find_image() {
    local candidates=() f d sum size newest=0 m
    if [ -n "$IMAGE_ARG" ]; then
        [ -f "$IMAGE_ARG" ] && candidates+=("$IMAGE_ARG")
    else
        local old
        old=$(shopt -p nullglob); shopt -s nullglob
        for d in "$HOME/Downloads" "$HOME/Desktop" "$HOME"; do
            for f in "$d"/$R_IMAGE_ZIP_PATTERN; do [ -f "$f" ] && candidates+=("$f"); done
        done
        eval "$old"
    fi
    for f in "${candidates[@]}"; do
        size=$(stat -L -c %s -- "$f")
        if [ "$size" != "$R_IMAGE_ZIP_SIZE" ]; then
            log "image candidate $f has $size bytes, expected $R_IMAGE_ZIP_SIZE"
            IMAGE_REJECTED+=("$f")
            continue
        fi
        out "      checking $f ($(gb "$size") GB; this takes up to a minute)..."
        sum=$(sha256sum -- "$f" | cut -c1-64)
        log "image candidate $f sha256=$sum"
        if [ "$sum" = "$R_IMAGE_ZIP_SHA256" ]; then
            m=$(stat -c %Y -- "$f")
            if [ "$m" -ge "$newest" ]; then newest=$m; IMAGE_FOUND=$f; fi
        else
            IMAGE_REJECTED+=("$f")
        fi
    done
}

# The case folder must exist (and be ours) before the image goes into it. When
# the kit is not installed yet, an empty case folder marked with .dfir-lab/ is
# created; installing the kit later carries disk/ over.
ensure_case_folder() {
    if [ -e "$CASE_DIR" ] && [ ! -d "$CASE_DIR/.dfir-lab" ]; then
        local aside
        aside="/cases/$R_CASE_ID.before-setup-$(date +%Y%m%d%H%M%S)"
        run sudo mv "$CASE_DIR" "$aside" || return 1
        NOTES+=("A folder $CASE_DIR that the setup did not create was moved to $aside.")
    fi
    sudo mkdir -p "$CASE_DIR/.dfir-lab" && run sudo chown root:root "$CASE_DIR" "$CASE_DIR/.dfir-lab" &&
        run sudo chmod 755 "$CASE_DIR" "$CASE_DIR/.dfir-lab"
}

install_image() {
    local zip=$IMAGE_FOUND dir=$R_IMAGE_ZIP_DIR list item name members=() expected
    # Free space: the unpacked files plus a margin, on the file system of /cases.
    local need=$((R_IMAGE_UNPACKED_BYTES + 536870912)) free_kb
    sudo mkdir -p /cases
    free_kb=$(df -Pk /cases | awk 'NR==2 {print $4}')
    log "image: free on /cases: $((free_kb / 1024)) MB, needed $((need / 1048576)) MB"
    if [ -n "$free_kb" ] && [ $((free_kb * 1024)) -lt "$need" ]; then
        incomplete "there is not enough free disk space to unpack the disk image: it needs $(gb "$need") GB, and only $(gb $((free_kb * 1024))) GB is free." \
            "Delete large files you do not need (not the disk image zip), then run the same setup command again."
        return 1
    fi
    # The zip must hold exactly the expected folder and files: no other paths, no links.
    list=$(unzip -Z1 "$zip" 2>> "$LOG") || { log "image: not a readable zip"; list=""; }
    expected=$(printf '%s/\n' "$dir"; for item in $R_IMAGE_FILES; do printf '%s/%s\n' "$dir" "${item%%:*}"; done)
    if [ -z "$list" ] || [ "$(printf '%s\n' "$list" | LC_ALL=C sort)" != "$(printf '%s\n' "$expected" | LC_ALL=C sort)" ] || \
       unzip -Z "$zip" 2>/dev/null | grep -q '^l'; then
        log "image: unexpected zip contents: $(printf '%s' "$list" | tr '\n' ' ')"
        incomplete "the disk image file $zip does not contain what the lab expects." \
            "Download $R_IMAGE_ZIP_NAME again from the course site; if it happens again, tell the TA."
        return 1
    fi
    for item in $R_IMAGE_FILES; do members+=("$dir/${item%%:*}"); done
    sudo rm -rf "$DISK_STAGING"
    sudo mkdir -p "$DISK_STAGING" || return 1
    out "      unpacking the disk image ($(gb "$R_IMAGE_UNPACKED_BYTES") GB; this takes a minute or two)..."
    if ! run sudo unzip -q -j "$zip" "${members[@]}" -d "$DISK_STAGING"; then
        incomplete "the disk image could not be unpacked." \
            "Download $R_IMAGE_ZIP_NAME again from the course site, then run the same setup command again."
        return 1
    fi
    # Root owns it; nobody can change it (mode 444 files in a 755 folder).
    run sudo chown -R root:root "$DISK_STAGING" && run sudo chmod 755 "$DISK_STAGING" || return 1
    for item in $R_IMAGE_FILES; do run sudo chmod 444 "$DISK_STAGING/${item%%:*}" || return 1; done
    local link target
    while read -r link target; do
        run sudo ln -sfn "$target" "$DISK_STAGING/$link" || return 1
    done < <(image_links)
    if ! image_folder_ok "$DISK_STAGING"; then
        incomplete "the unpacked disk image does not match its checksums: the download is damaged." \
            "Delete $zip, download $R_IMAGE_ZIP_NAME again from the course site, then run the same setup command again."
        return 1
    fi
    ensure_case_folder || return 1
    sudo rm -rf "$DISK_DIR"
    run sudo mv "$DISK_STAGING" "$DISK_DIR" || return 1
    return 0
}

offer_zip_delete() {
    local zip=$IMAGE_FOUND size
    case "$zip" in "$HOME"/*) ;; *) return ;; esac
    [ -w "$(dirname -- "$zip")" ] || return
    size=$(gb "$(stat -c %s -- "$zip")")
    if [ "$YES" -eq 1 ]; then
        note "kept $zip (--yes keeps it); delete it yourself to free $size GB"
        return
    fi
    if [ -t 0 ]; then
        local ans=""
        read -r -p "      The lab now uses the unpacked copy. Delete $zip to free $size GB? [Y/n] " ans
        log "answer: $ans"
        case "$ans" in
            ""|[Yy]*) if rm -f -- "$zip"; then ok "deleted $zip ($size GB freed)"; changed
                      else warn "could not delete $zip"; fi ;;
            *) note "kept $zip; you can delete it later to free $size GB" ;;
        esac
    else
        note "kept $zip; you can delete it to free $size GB (the lab uses the unpacked copy)"
    fi
}

disk_image() {
    step 7 "Installing the disk image ($R_IMAGE_ZIP_NAME, about 5 GB)"
    out "      The disk image holds live malware from a real intrusion: keep it inside this VM, never copy it to your own computer."
    if [ -n "$IMAGE_ARG" ] && [ ! -f "$IMAGE_ARG" ]; then
        incomplete "the disk image file $IMAGE_ARG does not exist." \
            "Check the file name after --image, or leave --image out to search ~/Downloads."
        return
    fi
    if [ -d "$DISK_DIR" ]; then
        out "      checking the installed disk image ($(gb "$R_IMAGE_UNPACKED_BYTES") GB)..."
        if image_folder_ok "$DISK_DIR"; then
            done_already "the disk image is in $DISK_DIR ($(image_sums | wc -l) files verified, read-only)"
            IMAGE_OK=1
            return
        fi
    fi
    find_image
    if [ -z "$IMAGE_FOUND" ]; then
        if [ ${#IMAGE_REJECTED[@]} -gt 0 ]; then
            incomplete "the disk image file ${IMAGE_REJECTED[0]} is damaged or incomplete (a download may have been cut short)." \
                "Delete it, download $R_IMAGE_ZIP_NAME again from the course site inside this VM, wait until the download has finished, then run the same setup command again."
        else
            incomplete "the disk image is not on this VM yet." \
                "Inside this VM, download the disk image ($R_IMAGE_ZIP_NAME, $(gb "$R_IMAGE_ZIP_SIZE") GB) from the course site; it goes to ~/Downloads. Then run the same setup command again."
        fi
        return
    fi
    if ! install_image; then
        [ ${#INCOMPLETE[@]} -gt 0 ] || incomplete "the disk image could not be installed." \
            "Run the same setup command again; if it happens again, tell the TA and send the log."
        return
    fi
    ok "disk image installed in $DISK_DIR (read-only; $R_IMAGE_SHORT_NAME.E01 is the short name)"
    IMAGE_OK=1
    changed
    offer_zip_delete
}

# ---------------------------------------------------------------------------
# Step 8: desktop comfort
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
    step 8 "Adjusting the desktop for the lab"
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
# Step 9: readiness check and summary
# ---------------------------------------------------------------------------

finish_checks() {
    step 9 "Checking the result"
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
    # The AI access the student chose (dfir-lab use); "duke" unless they switched.
    SETUP_ROUTE=$(printf '%s' "$json" | jq -r '.route // "duke"')
    for id in 1 2 3 4 5; do
        status=$(printf '%s' "$json" | jq -r ".results[] | select(.id == $id) | .status")
        result=$(printf '%s' "$json" | jq -r ".results[] | select(.id == $id) | .result")
        todo=$(printf '%s' "$json" | jq -r ".results[] | select(.id == $id) | .todo")
        name=$(printf '%s' "$json" | jq -r ".results[] | select(.id == $id) | .name")
        [ -n "$status" ] || continue
        if [ "$status" != "PASS" ] && { { [ "$CASE_OK" -ne 1 ] && { [ "$id" = 2 ] || [ "$id" = 3 ]; }; } ||
                                         { [ "$IMAGE_OK" -ne 1 ] && [ "$id" = 3 ]; }; }; then
            # Follows from the missing case files or disk image, which are already listed.
            out "      waiting: $name (needs the case files and the disk image)"
            continue
        fi
        case "$SETUP_ROUTE:$id:$status" in
            *:[123]:PASS) out "      passed: $name: $result" ;;
            *:[123]:WARN) out "      warning: $name: $result"; WARNINGS+=("$name: $result") ;;
            *:[123]:SKIP) out "      skipped: $name: $result" ;;
            *:[123]:*) incomplete "readiness check '$name' failed: $result" "${todo:-Tell the TA and send the log.}" ;;
            subscription:4:PASS) out "      passed: Claude subscription: Claude Code is ${result%%;*}" ;;
            subscription:4:*) out "      not yet: Claude Code is not signed in with your Claude subscription (run: dfir-lab use subscription)" ;;
            subscription:5:PASS) out "      passed: Anthropic's service: reachable (no VPN needed)" ;;
            subscription:5:*) out "      not now: Anthropic's service cannot be reached from this VM"
                 NOTES+=("Anthropic's service was not reachable during setup. Check that the VM has internet access; \"Check my setup\" tests it.") ;;
            *:4:PASS) out "      passed: Duke AI key: stored" ;;
            *:4:*) out "      not yet: no Duke AI key is stored (that is your next step)" ;;
            *:5:PASS) out "      passed: Duke AI gateway: reachable" ;;
            *:5:*) out "      not now: the Duke AI gateway cannot be reached from this VM"
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
    local di="not installed"
    [ "$IMAGE_OK" -eq 1 ] && di="$R_IMAGE_SHORT_NAME.E01   ($DISK_DIR; mount: dfir-lab mount)"
    out "   Claude Code     $cc"
    out "   Protocol SIFT   $psift"
    out "   Lab toolkit     $tk"
    out "   Case files      $cs"
    out "   Disk image      $di"
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
        if [ "$SETUP_ROUTE" = subscription ]; then
            out "   1. The lab uses your own Claude subscription (to use a Duke key instead: dfir-lab use duke)."
            out "   2. Double-click \"Check my setup\"."
        else
            out "   1. Double-click \"Set up Duke AI access\" on the desktop and paste your Duke AI key."
            out "   2. Double-click \"Check my setup\"."
            out " Optional: if you already pay for Claude (Pro or Max), you can use it instead of a Duke key; see the README."
        fi
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
    # The disk image must be unmounted before its files are removed.
    local ewf="/mnt/ewf_${R_IMAGE_SHORT_NAME,,}" res rc
    res=$(sudo bash "$PAYLOAD/lab/libexec/disk-mount" unmount "$DISK_DIR/$R_IMAGE_SHORT_NAME.E01" \
          "$ewf" "$R_IMAGE_MOUNT_POINT" 2>&1)
    rc=$?
    log "disk-mount unmount (exit $rc): $res"
    case "$rc" in
        0) out "  unmounted the disk image from $R_IMAGE_MOUNT_POINT" ;;
        11) ;;
        *) die "the disk image could not be unmounted from $R_IMAGE_MOUNT_POINT (${res##*RESULT })." \
               "Close every terminal and program that uses a folder inside $R_IMAGE_MOUNT_POINT (or restart the VM), then run the same command again. Nothing was removed." ;;
    esac
    sudo rmdir "$R_IMAGE_MOUNT_POINT" "$ewf" 2>/dev/null || true
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
    if [ -f "$UPDATE_AUTOSTART" ] && grep -q 'CYBERSEC 510 lab setup' "$UPDATE_AUTOSTART"; then
        rm -f "$UPDATE_AUTOSTART"
        out "  the Software Updater notifier starts again at the next login"
    fi
    if [ "$PURGE" -eq 1 ]; then
        rm -rf "$HOME/.local/bin/claude" "$HOME/.local/share/claude" "$HOME/.cache/claude" \
               "$HOME/.claude" "$HOME/.claude.json" "$HOME/.claude.json.backup"
        out "  removed Claude Code, ~/.claude (Protocol SIFT), your Duke AI key and ~/dfir-lab"
    else
        out "  kept: Claude Code, Protocol SIFT (~/.claude), your Duke AI key and your work"
    fi
    out "  not undone: the open-vm-tools-desktop package, the screen-lock and default-browser settings,"
    out "  and Prompt=never in $RELEASE_UPGRADES (no new-release prompt)"
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
    disk_image
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
ufv21Un/8M3bY70ble8eHx4c7J3Uv/397v4Pb83rz1/tvn2x139Ov/rHb7/b/evegX3Vv/di/3j3
u1d7/YNDQOLSQ/u7r/onR7svX+4/b0Xm7u7bk8O3b17snuwdtaL9Y/rk68NGwql8KO9zPk3nQ02o
rmr8EK2h4de7Rz/snbx5tftc2tw/OD7ZffWKmjrO2CjKuajDUc5AKqjiQWrvaABHJClkc6lFr0s5
vbgYDRCeMhsvL0eTxk06v84WnCfM1QqX+RJoTtFwKbWJopt0QOwBtliJ2oBvFY5NIHrQmk8GV/Gz
jefGT3CBT3G0KIx97+XLw6OT/qu9H/deNQxeQVXrfzS1SSfRq8PdF/2jvRdvn3M+997Bj5qZRHvU
K6niL/7um/3+D3s/+/uFJvb7/snhD3sHBiehQYLFuxECviQmBg5HvytvaY9+t/fi6PD5D9FW1P9x
7+hk71/x18vDtwcvjn6Othp9r/2fjnHvNV15tSdeBy3BLu5VXoKhtTW440mz651btsGNFq4eWzob
/ZDdfZ+NZ9xRPmqhy3Wam7/mmflLsHTsLwvuK15T2HWJTxiXKfDDi45ZicoircGG6Vd6Zj18fkBb
/JjOR9ikuZhZBF8e8QNRAW2OZKYb6wqYZwhTw6YlAQQ3qCW5ZVcIhChLh5Zgia8aEwzEheWlxB1d
ySwlgo0RUiMgcw3woUEmoJhloraVTbaKLy0ZbAzakXyaui4xT/66tAT/Qq6H0W2aJ9bsNJ7TFtbT
8uPuEeAtSKMp7lgWlyr2LOk3b44Of9x/sXfk3q7ZrCyKFW7J5q26o5u5SmotPRvs9qrGZPdTZ58f
Hrx8tU8H9uCvpr/F8X8VBSPC9gG549A1v3ySJq17kfQRF4TimpV0WG4U9oIE8AuSYIE/ItWPcq2y
VBFtOM9uaK0YluTGHTVbDYLdtYKLwje9zUt00j+7huxHl1MOb5wG9RXScT7lHORc4VoU/IoBYYI2
pTAjj2JL0yqSui1qOC/tqRdvfyA+cvDq56otZZhsuKfKXLVSYYlXc9rqNj1eW91oPf8t7iafUeCe
zjPyW+5Fz+nM34yWNwZZ5sfXNIVAYgFLO4dOAirwcjTPLqbvHU/CE3h+njVNhRk0BpL0sLPTefTw
Iccr5AE8ziy91BJf//zd0eFPx+D1rOm9H1628XiUTM9zKaJJ/PYev3w+mqTzu2eRvlE0NOsCIkYa
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
