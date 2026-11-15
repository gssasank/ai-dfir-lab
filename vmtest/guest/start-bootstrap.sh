#!/bin/bash
# Starts the student's one-line setup command in the guest the way a student
# runs it: as sansforensics, in the logged-in GNOME session's environment
# (systemd user manager: DISPLAY, WAYLAND_DISPLAY, D-Bus session bus), from the
# home folder, in an interactive bash that has read ~/.bashrc (so noclobber is
# on, as in a GNOME Terminal). It runs as a transient systemd user unit so it
# keeps running if open-vm-tools restarts, and returns at once.
#
#   start-bootstrap.sh UNIT OUTDIR [pty]
#
# OUTDIR/command.txt must already hold the one-line command. Afterwards
# OUTDIR/screen.txt has everything printed to the terminal and OUTDIR/rc the
# exit code. OUTDIR/before.txt records time, bytes received and disk use.
# With "pty", the command runs in a pseudo-terminal (pty-run.py, next to this
# script) that answers its [Y/n] questions with y; otherwise stdin is /dev/null.
set -u
unit=$1
out=$2
mode=${3:-plain}
here=$(cd "$(dirname "$0")" && pwd)
uid=$(id -u)
export XDG_RUNTIME_DIR=/run/user/$uid
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
[ -s "$out/command.txt" ] || { echo "missing $out/command.txt"; exit 2; }
rm -f "$out/rc" "$out/screen.txt" "$out/after.txt"
rx() { local t=0 f n; for f in /sys/class/net/*/statistics/rx_bytes; do
         case "$f" in */lo/*|*/docker*|*/veth*) continue ;; esac; read -r n < "$f"; t=$((t + n)); done; echo "$t"; }
printf 'epoch=%s\nrx=%s\ndf_used=%s\n' "$(date +%s)" "$(rx)" "$(df -B1 --output=used / | tail -n1 | tr -d ' ')" > "$out/before.txt"
systemctl --user reset-failed "$unit.service" 2>/dev/null || true
systemd-run --user --collect --quiet --unit="$unit" --working-directory="$HOME" \
    /bin/bash -c '
        if [ "$2" = pty ]; then
            python3 "$3/pty-run.py" "$1" y
        else
            bash -i -c "$(cat "$1/command.txt")" > "$1/screen.txt" 2>&1 < /dev/null
        fi
        rc=$?
        t=0; for f in /sys/class/net/*/statistics/rx_bytes; do
            case "$f" in */lo/*|*/docker*|*/veth*) continue ;; esac; read -r n < "$f"; t=$((t + n)); done
        printf "epoch=%s\nrx=%s\ndf_used=%s\n" "$(date +%s)" "$t" "$(df -B1 --output=used / | tail -n1 | tr -d " ")" > "$1/after.txt"
        echo "$rc" > "$1/rc"' bash "$out" "$mode" "$here"
echo "started unit $unit (rc of systemd-run: $?)"
