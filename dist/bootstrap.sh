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
R_VERSION='1.0.0'
R_CLAUDE_CODE_VERSION='2.1.322'
R_PROTOCOL_SIFT_REPO='https://github.com/teamdfir/protocol-sift.git'
R_PROTOCOL_SIFT_COMMIT='40bed7a96bfd986ea048c3b2aeb9d788b2f3400c'
R_GATEWAY_BASE_URL='https://litellm.oit.duke.edu'
R_CASE_ID='szechuan'
R_CASE_TITLE='Szechuan Sauce'
R_CASE_KIT_PATTERN='szechuan-kit-*.zip'
R_CASE_KIT_SHA256='86a32199fc9d506afb785f4f6705be84484f510027dc12d79533fbd1fa4107ed'
R_CASE_KIT_URL='https://raw.githubusercontent.com/gssasank/ai-dfir-lab/v1.0.0/kit/szechuan-kit-0.1.0.zip'
R_IMAGE_ZIP_PATTERN='DC01-E01*.zip'
R_IMAGE_ZIP_NAME='DC01-E01.zip'
R_IMAGE_ZIP_SIZE='4836649413'
R_IMAGE_ZIP_SHA256='efe06d12388dbc000fa4ae306746ddaca3893a6cdbd55311b52f5833e717acd9'
R_IMAGE_ZIP_DIR='E01-DC01'
R_IMAGE_FILES='20200918_0347_CDrive.E01:c0c8fe58dcdb65f6574cb63b34f086856087cfaf06872ed702a4c0c264446bac 20200918_0347_CDrive.E02:73f87df9de0fa9ed41c6fdc360f0e15c3d13955e7fd24f5e59a5b2e282d6b384 20200918_0347_CDrive.E01.txt:af08169bc8c32b27fbaa85f26646f8c1bb9f5f71f0ee04a759f9064928c8ca79'
R_IMAGE_UNPACKED_BYTES='4872432596'
R_IMAGE_SHORT_NAME='DC01'
R_IMAGE_MOUNT_POINT='/mnt/dc01'
R_PAYLOAD_SHA256='05890095f824c988214806195ae9de05a5bcbf1525b21b6149117456794907f4'
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
SHORTCUTS="dfir-lab-setup-key dfir-lab-start dfir-lab-check dfir-lab-replay"
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
/wecxFSRACgFAA==
