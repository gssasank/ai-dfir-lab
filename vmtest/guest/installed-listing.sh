#!/bin/bash
# Prints a sorted listing of everything the lab setup script installs or
# changes: for each path its type, owner, mode, size and SHA-256, plus the
# desktop settings and packages it changes. Two listings taken before and
# after a second run must be identical. Run as sansforensics (uses sudo).
#
#   installed-listing.sh CASE_ID
set -u
case_id=${1:-szechuan}
uid=$(id -u)
export XDG_RUNTIME_DIR=/run/user/$uid
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
H=$HOME
paths=(/usr/local/bin/dfir-lab /usr/local/bin/dfir-lab-configure-claude /usr/local/lib/dfir-lab
       /etc/dfir-lab /usr/local/share/applications "/cases/$case_id" "$H/Desktop" "$H/.claude"
       "$H/.claude.json" "$H/.bashrc" "$H/.profile" "$H/.local/bin" "$H/.local/share/claude"
       "$H/.config/dfir-lab" "$H/.config/mimeapps.list" "$H/.config/autostart"
       /etc/update-manager/release-upgrades)
existing=()
for p in "${paths[@]}"; do sudo test -e "$p" && existing+=("$p"); done
sudo find "${existing[@]}" -printf '%y %u:%g %m %s %p\n' 2>/dev/null | LC_ALL=C sort -k5 |
while read -r type owner mode size path; do
    if [ "$type" = "f" ]; then
        sum=$(sudo sha256sum -- "$path" | cut -c1-64)
    elif [ "$type" = "l" ]; then
        sum="-> $(sudo readlink -- "$path")"
    else
        sum="-"; size="-"
    fi
    printf '%s %s %s %s %s %s\n' "$type" "$owner" "$mode" "$size" "$sum" "$path"
done
for f in "$H"/Desktop/*.desktop; do
    [ -f "$f" ] && printf 'trusted %s %s\n' "$(gio info -a metadata::trusted "$f" 2>/dev/null | awk '/metadata::trusted:/ {print $2}')" "$f"
done
printf 'gsettings idle-delay %s\n' "$(gsettings get org.gnome.desktop.session idle-delay 2>&1)"
printf 'gsettings lock-enabled %s\n' "$(gsettings get org.gnome.desktop.screensaver lock-enabled 2>&1)"
printf 'gsettings ubuntu-lock-on-suspend %s\n' "$(gsettings get org.gnome.desktop.screensaver ubuntu-lock-on-suspend 2>&1)"
printf 'browser %s\n' "$(xdg-settings get default-web-browser 2>&1)"
printf 'mime https %s\n' "$(xdg-mime query default x-scheme-handler/https 2>&1)"
dpkg-query -W -f='package ${Package} ${Version} ${Status}\n' open-vm-tools-desktop zenity 2>&1
printf 'mount dc01 %s\n' "$(findmnt -rn -o SOURCE,FSTYPE,VFS-OPTIONS --mountpoint /mnt/dc01 2>&1 | tr '\n' ';')"
printf 'release-upgrades %s\n' "$(grep '^Prompt=' /etc/update-manager/release-upgrades 2>&1)"
