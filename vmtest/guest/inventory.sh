#!/bin/sh
# Read-only inventory of a SIFT guest, one section per call:  sh inventory.sh <section>
# Sections: system accounts packages-user packages-root network languages ai forensic
#           evtx-test desktop sift root
# Sections ending in -root and "root" must run as root (Invoke-Guest.ps1 -Sudo); the others
# as the desktop user with Invoke-Guest.ps1 -DesktopEnv.
# Changes it makes: "packages-root" runs apt-get update (refreshes /var/lib/apt/lists);
# "evtx-test" and "languages" write only under /tmp; "system" starts Firefox headless once.
# It never prints secret values: credential-like files are listed by path, size and mode only.

T=${T:-120}
r() {
  printf '\n### $ %s\n' "$1"
  timeout -k 5 "$T" sh -c "$1" 2>&1
  printf '[exit=%s]\n' "$?"
}
hdr() { printf '\n======== %s ========\n' "$1"; }
U=sansforensics
H=/home/$U

case "$1" in

system)
  hdr "OS"
  r 'cat /etc/os-release'
  r 'lsb_release -a'
  r 'cat /etc/lsb-release'
  r 'uname -a'
  r 'uname -m; dpkg --print-architecture; dpkg --print-foreign-architectures'
  r 'ldd --version | head -1; getconf GNU_LIBC_VERSION'
  r 'cat /proc/cmdline'
  hdr "Desktop session and display server"
  r 'loginctl list-sessions --no-legend'
  r 'for s in $(loginctl list-sessions --no-legend | awk "{print \$1}"); do echo "--- session $s"; loginctl show-session "$s" -p Name -p Type -p Class -p Desktop -p Service -p Seat -p State -p Remote; done'
  r 'p=$(pgrep -u '$U' -x gnome-shell | head -1); echo "gnome-shell pid=$p"; tr "\0" "\n" < /proc/$p/environ | grep -E "^(XDG_SESSION_TYPE|XDG_CURRENT_DESKTOP|XDG_SESSION_DESKTOP|DESKTOP_SESSION|GDMSESSION|WAYLAND_DISPLAY|DISPLAY|LANG|LANGUAGE|LC_[A-Z]+|PATH|SHELL)="'
  r 'ps -eo pid,user,args | grep -E "[X]wayland|[X]org|[g]nome-shell$|[g]dm-wayland|[g]dm-x-session" '
  r 'gnome-shell --version'
  r 'cat /etc/gdm3/custom.conf'
  hdr "Default terminal emulator"
  r 'gsettings get org.gnome.desktop.default-applications.terminal exec'
  r 'update-alternatives --query x-terminal-emulator'
  r 'for t in gnome-terminal ptyxis kgx xterm terminator tilix konsole xfce4-terminal; do printf "%s: " $t; command -v $t || echo absent; done'
  r 'gnome-terminal --version'
  r 'gsettings get org.gnome.shell favorite-apps'
  hdr "Default browser"
  r 'xdg-settings get default-web-browser'
  r 'xdg-mime query default x-scheme-handler/https; xdg-mime query default text/html'
  r 'update-alternatives --query x-www-browser'
  r 'for b in firefox chromium chromium-browser google-chrome; do printf "%s: " $b; command -v $b || echo absent; done; ls -l /usr/bin/firefox 2>&1; cat /usr/bin/firefox 2>/dev/null | head -5'
  r 'snap list firefox 2>&1; dpkg -l firefox 2>&1 | tail -2'
  r 'firefox --version'
  T=90 r 'd=$(mktemp -d /tmp/ffprobe.XXXX); cd "$d"; s=$(date +%s); firefox --headless --screenshot "$d/shot.png" about:blank >"$d/log" 2>&1; rc=$?; e=$(date +%s); echo "firefox headless rc=$rc seconds=$((e-s))"; ls -l "$d"; tail -15 "$d/log"'
  hdr "Time, locale, NTP"
  r 'timedatectl'
  r 'timedatectl show; timedatectl show-timesync 2>&1'
  r 'cat /etc/timezone; ls -l /etc/localtime'
  r 'systemctl is-enabled systemd-timesyncd chrony ntp ntpsec 2>&1; systemctl is-active systemd-timesyncd chrony ntp ntpsec 2>&1'
  r 'locale'
  r 'localectl'
  r 'cat /etc/default/locale; locale -a'
  hdr "Disk, memory, CPU, host"
  r 'lsblk -o NAME,SIZE,TYPE,FSTYPE,FSVER,LABEL,MOUNTPOINTS'
  r 'df -hT -x tmpfs -x devtmpfs -x squashfs -x overlay'
  r 'df -hT'
  r 'cat /etc/fstab'
  r 'swapon --show; cat /proc/swaps'
  r 'free -h'
  r 'nproc; lscpu'
  r 'grep -m1 "model name" /proc/cpuinfo; grep -c ^processor /proc/cpuinfo'
  r 'hostnamectl'
  r 'hostname; hostname -f; cat /etc/hostname; cat /etc/hosts'
  r 'cat /etc/machine-id; cat /var/lib/dbus/machine-id'
  hdr "Boot timing and service health"
  r 'uptime; cat /proc/uptime'
  r 'systemd-analyze'
  r 'systemd-analyze blame | head -30'
  r 'systemd-analyze critical-chain'
  r 'systemd-analyze critical-chain open-vm-tools.service'
  r 'systemctl --failed --no-legend'
  r 'systemctl status open-vm-tools.service --no-pager'
  r 'systemctl is-system-running'
  r 'systemd-detect-virt'
  ;;

accounts)
  hdr "User"
  r 'id; id '$U'; getent passwd '$U'; getent group sudo adm docker lxd wireshark vboxsf 2>&1'
  r 'ls -la '$H
  r 'ls -la '$H'/Desktop'
  r 'cat '$H'/.bashrc | tail -40'
  r 'cat '$H'/.profile'
  r 'ls -la /etc/profile.d; cat /etc/environment'
  hdr "sudo (as the user, no password supplied)"
  r 'sudo -n true && echo "sudo -n: NO PASSWORD REQUIRED" || echo "sudo -n: password required"'
  r 'sudo -n -l'
  hdr "SSH server"
  r 'dpkg -l openssh-server openssh-client 2>&1 | tail -3'
  r 'systemctl is-enabled ssh.service ssh.socket sshd.service 2>&1; systemctl is-active ssh.service ssh.socket 2>&1'
  r 'ss -tlnp'
  r 'ls -l /etc/ssh/ 2>&1'
  hdr "Autologin and screen lock"
  r 'grep -iE "^\s*#?\s*(AutomaticLogin|WaylandEnable|TimedLogin)" /etc/gdm3/custom.conf'
  r 'for k in "org.gnome.desktop.screensaver lock-enabled" "org.gnome.desktop.screensaver lock-delay" "org.gnome.desktop.screensaver idle-activation-enabled" "org.gnome.desktop.screensaver ubuntu-lock-on-suspend" "org.gnome.desktop.session idle-delay" "org.gnome.desktop.lockdown disable-lock-screen" "org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type" "org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout" "org.gnome.settings-daemon.plugins.power idle-dim" "org.gnome.desktop.notifications show-banners"; do printf "%s = " "$k"; gsettings get $k 2>&1; done'
  r 'ls -la /etc/dconf/db /etc/dconf/profile 2>&1; cat /etc/dconf/profile/* 2>/dev/null; find /etc/dconf/db -type f -name "*" ! -name "*.gvdb" -exec sh -c "echo --- {}; cat {}" \; 2>/dev/null'
  r 'ls -la '$H'/.config/dconf 2>&1; dconf dump / 2>&1 | head -120'
  ;;

packages-user)
  hdr "apt state before update (as user)"
  r 'ls -la --time-style=full-iso /var/lib/apt/lists | head -40'
  r 'stat -c "%y %n" /var/lib/apt/lists/partial /var/cache/apt/pkgcache.bin /var/lib/dpkg/status 2>&1'
  r 'cat /etc/apt/sources.list 2>&1; ls -la /etc/apt/sources.list.d; for f in /etc/apt/sources.list.d/*; do echo "--- $f"; cat "$f"; done'
  r 'ls -la /etc/apt/trusted.gpg.d /etc/apt/keyrings /usr/share/keyrings 2>&1'
  r 'ps -eo pid,user,etime,args | grep -E "[a]pt|[d]pkg|[u]nattended|[p]ackagekit" '
  r 'cat /etc/apt/apt.conf.d/20auto-upgrades 2>&1; cat /etc/apt/apt.conf.d/10periodic 2>&1'
  r 'apt-config dump | grep -iE "^APT::Periodic|Unattended-Upgrade::(Allowed|Origins|Automatic-Reboot|Remove)" '
  r 'grep -vE "^\s*//|^\s*$" /etc/apt/apt.conf.d/50unattended-upgrades 2>&1 | head -60'
  r 'dpkg -l unattended-upgrades update-notifier update-manager packagekit 2>&1 | tail -5'
  r 'systemctl list-timers --all --no-pager'
  r 'systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service packagekit.service 2>&1; systemctl is-active apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service packagekit.service apt-daily.service apt-daily-upgrade.service 2>&1'
  r 'systemctl status apt-daily.service apt-daily-upgrade.service --no-pager 2>&1 | head -40'
  r 'cat /etc/systemd/system/timers.target.wants/* 2>/dev/null | head -5; ls -la /etc/systemd/system/timers.target.wants/'
  r 'ls -la /var/log/unattended-upgrades/ 2>&1; tail -20 /var/log/unattended-upgrades/unattended-upgrades.log 2>&1'
  r 'tail -30 /var/log/apt/history.log 2>&1'
  r 'ls -la /var/log/apt/ 2>&1; zcat -f /var/log/apt/history.log* 2>/dev/null | grep -E "^Start-Date" | tail -5'
  hdr "Package counts"
  r 'dpkg -l | grep -c "^ii"; dpkg -l | grep -v "^ii" | grep -vE "^(Desired|\||\+)" | head'
  r 'dpkg --audit; echo audit-rc=$?'
  r 'apt-get check 2>&1 | tail -5'
  hdr "snap"
  r 'command -v snap; snap version'
  r 'snap list'
  r 'systemctl is-active snapd.service snapd.socket; snap changes 2>&1 | tail -10'
  r 'snap refresh --time 2>&1'
  hdr "pip/pipx global"
  r 'pip3 list 2>/dev/null | wc -l; ls /usr/local/lib/python3*/dist-packages 2>/dev/null | head -60'
  ;;

packages-root)
  hdr "apt locks right now"
  r 'for l in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock; do printf "%s: " $l; fuser -v $l 2>&1 || echo free; done'
  r 'ps -eo pid,user,etime,args | grep -E "[a]pt|[d]pkg|[u]nattended" '
  hdr "apt-get update (no upgrade, no install)"
  T=600 r 's=$(date +%s); apt-get update; rc=$?; echo "apt-get update rc=$rc seconds=$(( $(date +%s) - s ))"'
  r 'apt-get -s -o Debug::NoLocking=1 upgrade 2>&1 | tail -5'
  r 'apt list --upgradable 2>/dev/null | wc -l; apt list --upgradable 2>/dev/null | head -40'
  r 'apt-cache policy | head -80'
  r 'journalctl -b -u apt-daily.service -u apt-daily-upgrade.service -u unattended-upgrades.service --no-pager 2>&1 | tail -30'
  ;;

network)
  hdr "Addresses, routes, DNS"
  r 'ip -br addr; ip -br link'
  r 'ip route'
  r 'ethtool -i ens33 2>&1 | head -3; lspci -nn | grep -iE "ethernet|vga|scsi|sata|usb|audio"'
  r 'ls -l /etc/resolv.conf; cat /etc/resolv.conf'
  r 'resolvectl status 2>&1 | head -40'
  r 'cat /etc/netplan/*.yaml 2>&1; nmcli -t dev 2>&1; nmcli -t con show 2>&1'
  r 'env | grep -iE "^(http|https|no|all)_proxy=" ; cat /etc/apt/apt.conf.d/*proxy* 2>/dev/null; echo "(proxy check done)"'
  hdr "HTTPS reachability"
  for h in github.com raw.githubusercontent.com objects.githubusercontent.com claude.ai downloads.claude.ai storage.googleapis.com; do
    T=45 r "getent ahosts $h | head -3; curl -sS -o /dev/null --max-time 30 -w 'http_code=%{http_code} remote_ip=%{remote_ip} t_dns=%{time_namelookup} t_connect=%{time_connect} t_tls=%{time_appconnect} t_total=%{time_total} ssl_verify=%{ssl_verify_result}\n' https://$h/"
  done
  hdr "litellm.oit.duke.edu (expected to fail from this network)"
  r 'getent ahosts litellm.oit.duke.edu; echo getent-rc=$?'
  r 'cat /proc/sys/net/ipv4/tcp_syn_retries'
  T=340 r 's=$(date +%s.%N); curl -sS -o /dev/null -w "http_code=%{http_code} remote_ip=%{remote_ip} t_connect=%{time_connect} t_total=%{time_total}\n" https://litellm.oit.duke.edu/; rc=$?; e=$(date +%s.%N); echo "curl rc=$rc elapsed_seconds=$(echo "$e - $s" | bc 2>/dev/null || awk "BEGIN{print $e-$s}")"'
  T=60 r 's=$(date +%s); curl -sS -o /dev/null --connect-timeout 10 https://litellm.oit.duke.edu/; echo "with --connect-timeout 10: rc=$? seconds=$(( $(date +%s) - s ))"'
  ;;

languages)
  hdr "Interpreters and basic tools"
  for b in python3 python pip3 pip pipx bash sh curl wget git jq unzip 7z 7za 7zz zenity gio xdg-open node npm npx dotnet rg perl ruby java go gcc make docker pwsh uv; do
    p=$(command -v $b 2>/dev/null)
    printf '%-10s %s' "$b" "${p:-ABSENT}"
    [ -n "$p" ] && printf '  -> %s' "$(readlink -f "$p")"
    echo
  done
  r 'python3 --version; python3 -c "import sys; print(sys.executable, sys.version)"'
  r 'python3 -c "import venv, ensurepip; print(\"venv+ensurepip import ok\")"'
  r 'python3 -m pip --version; pip3 --version'
  r 'dpkg -l python3 python3-venv python3-pip python3-full "python3.*-venv" pipx 2>&1 | grep -E "^(ii|un|rc)"'
  r 'rm -rf /tmp/vt-probe; python3 -m venv /tmp/vt-probe && /tmp/vt-probe/bin/python -m pip --version; echo venv-rc=$?; rm -rf /tmp/vt-probe'
  r 'python3 -m pip install --dry-run requests 2>&1 | tail -3'
  r 'bash --version | head -1'
  r 'curl --version | head -2'
  r 'wget --version | head -1'
  r 'git --version'
  r 'jq --version'
  r 'unzip -v | head -1'
  r '7z 2>&1 | head -3; 7za 2>&1 | head -3; 7zz 2>&1 | head -3'
  r 'zenity --version'
  r 'gio version'
  r 'xdg-open --version'
  r 'node --version; npm --version; ls -d /usr/lib/node_modules/* 2>/dev/null'
  r 'dotnet --list-runtimes'
  r 'dotnet --list-sdks'
  r 'dotnet --info 2>&1 | head -40'
  r 'ls -la /usr/share/dotnet /usr/lib/dotnet /opt/dotnet /usr/local/share/dotnet '$H'/.dotnet 2>&1; dpkg -l | grep -iE "dotnet|aspnetcore" '
  r 'rg --version'
  r 'perl --version | head -2; gcc --version | head -1; make --version | head -1; docker --version'
  ;;

ai)
  hdr "Claude Code"
  r 'command -v claude; echo rc=$?'
  r 'ls -la /usr/local/bin/claude '$H'/.local/bin '$H'/.local/bin/claude 2>&1'
  r 'ls -la '$H'/.claude '$H'/.claude.json '$H'/.config/claude 2>&1'
  r 'find '$H'/.claude -maxdepth 3 2>/dev/null | head -50'
  r 'ls -la '$H'/.npm-global/bin 2>&1; npm ls -g --depth=0 2>&1 | head'
  hdr "Protocol SIFT / Mulder / TRUDI (name search, user-readable paths)"
  r 'grep -il -E "protocol.?sift|mulder|trudi|claude" '$H'/.bashrc '$H'/.profile /etc/bash.bashrc /etc/profile.d/* 2>/dev/null; echo grep-rc=$?'
  r 'ls -la /opt'
  r 'ls -la /usr/local/bin | grep -iE "claude|mulder|trudi|protocol|sift|mcp" '
  ;;

root)
  hdr "sudoers"
  r 'cat /etc/sudoers | grep -vE "^\s*#|^\s*$"'
  r 'ls -la /etc/sudoers.d; for f in /etc/sudoers.d/*; do echo "--- $f"; grep -vE "^\s*#|^\s*$" "$f"; done'
  r 'passwd -S '$U'; passwd -S root; chage -l '$U
  hdr "Disk detail"
  r 'fdisk -l /dev/sda'
  r 'pvs; vgs; lvs'
  r 'tune2fs -l $(findmnt -no SOURCE /) 2>&1 | grep -E "Filesystem created|Last mount time|Last write time|Mount count|Filesystem features"'
  hdr "Boot journal (warnings and tools)"
  r 'journalctl -b -u open-vm-tools.service --no-pager | head -30'
  r 'journalctl -b -p warning --no-pager | tail -60'
  r 'journalctl --list-boots --no-pager'
  r 'journalctl -b --no-pager | grep -iE "time has been changed|clock change|timesync|Contacted time server|Initial clock" | head -20'
  r 'journalctl -b --no-pager | head -3; journalctl -b --no-pager -o short-monotonic | grep -iE "vmtoolsd|vgauth|open-vm" | head -25'
  hdr "apt configuration files"
  r 'ls -la /etc/apt/apt.conf.d; for f in /etc/apt/apt.conf.d/*; do echo "--- $f"; grep -vE "^\s*//|^\s*$|^\s*#" "$f" | head -15; done'
  hdr "cloud-init"
  r 'cloud-init status --long 2>&1; ls -la /etc/cloud/cloud-init.disabled /etc/cloud/cloud.cfg.d 2>&1'
  r 'cat /etc/default/grub | grep -vE "^\s*#|^\s*$"; grep -m3 -n "autoinstall" /boot/grub/grub.cfg'
  hdr "Listening services"
  r 'ss -tlnp; ss -ulnp | head -20'
  r 'systemctl list-units --type=service --state=running --no-pager --no-legend'
  hdr "Name search for Claude / Protocol SIFT / Mulder / TRUDI (whole root filesystem)"
  T=600 r 'find / -xdev \( -path /proc -o -path /sys -o -path /snap \) -prune -o \( -iname "*claude*" -o -iname "*protocol*sift*" -o -iname "*protocolsift*" -o -iname "*mulder*" -o -iname "*trudi*" -o -iname "*anthropic*" \) -print 2>/dev/null | head -100'
  hdr "Credential-like files (paths, sizes and modes only; contents never printed)"
  T=600 r 'find /home /root /opt /etc /srv /usr/local -xdev \( -name ".env" -o -name "*.pem" -o -name "id_rsa*" -o -name "id_ed25519*" -o -name "id_ecdsa*" -o -name "credentials" -o -name "credentials.json" -o -name ".credentials.json" -o -name ".netrc" -o -name ".git-credentials" -o -name "*.key" -o -name "hosts.yml" -o -name "config.json" -path "*docker*" -o -name "*.p12" -o -name "*.pfx" -o -name "*token*" \) -not -path "/etc/ssl/certs/*" -not -path "/usr/share/*" -printf "%m %u %s %p\n" 2>/dev/null | head -80'
  r 'ls -la /root /root/.ssh /home/'$U'/.ssh /home/'$U'/.aws /home/'$U'/.config/gh 2>&1'
  r 'for f in /home/'$U'/.bash_history /root/.bash_history /home/'$U'/.python_history /home/'$U'/.lesshst; do [ -e "$f" ] && stat -c "%s bytes %y %n" "$f"; done; echo done'
  r 'ls -l /etc/ssh/ssh_host_* 2>&1'
  ;;

forensic)
  hdr "/usr/local/bin and /opt/zimmermantools"
  r 'ls -la /usr/local/bin'
  r 'ls -la /opt'
  r 'find /opt/zimmermantools -maxdepth 2 | sort'
  r 'du -sh /opt/zimmermantools 2>&1'
  r 'for f in /usr/local/bin/*; do if [ -f "$f" ] && [ $(stat -c %s "$f") -lt 4096 ] && grep -qi zimmerman "$f" 2>/dev/null; then echo "--- $f"; cat "$f"; fi; done'
  r 'for f in /opt/zimmermantools/*.dll /opt/zimmermantools/*/*.dll; do :; done; ls /opt/zimmermantools/*.runtimeconfig.json /opt/zimmermantools/*/*.runtimeconfig.json 2>/dev/null | head -3 | while read f; do echo "--- $f"; cat "$f"; done'
  hdr "EvtxECmd"
  r 'command -v evtxecmd EvtxECmd EvtxeCmd; ls -la /usr/local/bin | grep -i evtx'
  r 'file $(command -v evtxecmd || command -v EvtxECmd) 2>&1; cat $(command -v evtxecmd || command -v EvtxECmd) 2>&1 | head -20'
  r 'evtxecmd --help'
  r 'EvtxECmd --help 2>&1 | head -5'
  r 'ls /opt/zimmermantools/EvtxeCmd /opt/zimmermantools/EvtxECmd 2>&1 | head; ls /opt/zimmermantools/EvtxeCmd/Maps 2>/dev/null | wc -l'
  hdr "python-evtx"
  r 'ls -la /usr/local/bin/evtx* /usr/bin/evtx* 2>&1'
  r 'command -v evtx_dump.py evtx_dump_json.py evtx_info.py evtx_dump_chunk_slack.py evtx_eid_record_numbers.py evtx_filter_records.py evtx_record_structure.py evtx_structure.py evtx_templates.py'
  r 'head -3 $(command -v evtx_dump.py)'
  r 'evtx_dump.py --help'
  r 'python3 -m pip show python-evtx 2>&1 | head -5'
  hdr "libevtx"
  r 'evtxexport -V; dpkg -l libevtx-utils 2>&1 | tail -1'
  r 'evtxinfo -V'
  hdr "Plaso"
  r 'command -v log2timeline.py psort.py pinfo.py psteal.py log2timeline psort'
  r 'log2timeline.py --version'
  r 'psort.py --version'
  r 'dpkg -l | grep -iE "plaso|python3-plaso"; python3 -m pip show plaso 2>&1 | head -3'
  hdr "RegRipper"
  r 'command -v rip.pl regripper; ls -la $(command -v rip.pl) 2>&1'
  r 'rip.pl -h 2>&1 | head -25'
  hdr "Sleuth Kit / libewf"
  r 'mmls -V; fls -V'
  r 'fls -i list'
  r 'mmls -i list'
  r 'img_stat -i list'
  r 'dpkg -l sleuthkit libtsk19 libtsk-dev libewf2 ewf-tools libewf-dev 2>&1 | grep -E "^(ii|un)"'
  r 'ewfmount -V; ewfverify -V; ewfinfo -V'
  r 'command -v ewfmount ewfverify ewfinfo ewfexport ewfacquire'
  hdr "Other tools"
  r 'command -v hayabusa chainsaw; hayabusa --version 2>&1 | head -3; chainsaw --version 2>&1 | head -3'
  r 'command -v vol vol.py vol3 volatility volatility3 vol2'
  r 'vol --help 2>&1 | head -5'
  r 'vol.py --help 2>&1 | head -5; head -1 $(command -v vol.py) 2>&1'
  r 'vol3 --help 2>&1 | head -3'
  r 'yara --version; command -v yara yarac'
  r 'tshark --version | head -2; command -v tshark'
  r 'command -v bulk_extractor foremost scalpel photorec testdisk regipy-dump analyzeMFT.py mftecmd MFTECmd usnparser artifactcollector'
  ;;

evtx-test)
  REPO_COMMIT=474856008f037ccd42753f02a631b42690195829
  URL="https://raw.githubusercontent.com/mdecrevoisier/EVTX-to-MITRE-Attack/$REPO_COMMIT/TA0005-Defense%20Evasion/T1562.002-Disable%20Windows%20Event%20Logging/ID4719-Audit%20policy%20deactivation.evtx"
  W=/tmp/evtx-test
  rm -rf $W; mkdir -p $W/out
  hdr "Download sample (CC0, mdecrevoisier/EVTX-to-MITRE-Attack @ $REPO_COMMIT)"
  r "curl -fsSL -o $W/sample.evtx '$URL'; echo curl-rc=\$?"
  r "ls -l $W/sample.evtx; sha256sum $W/sample.evtx; file $W/sample.evtx; head -c 8 $W/sample.evtx | od -c | head -1"
  hdr "EvtxECmd -> CSV"
  T=300 r "cd $W && evtxecmd -f $W/sample.evtx --csv $W/out --csvf evtxecmd.csv > $W/evtxecmd.log 2>&1; echo evtxecmd-rc=\$?; cat $W/evtxecmd.log"
  r "ls -l $W/out; head -c 3000 $W/out/evtxecmd.csv; echo; wc -l $W/out/evtxecmd.csv"
  hdr "python-evtx evtx_dump.py -> XML"
  r "evtx_dump.py $W/sample.evtx > $W/out/evtx_dump.xml 2> $W/evtx_dump.err; echo rc=\$?; cat $W/evtx_dump.err | tail -20; wc -c $W/out/evtx_dump.xml; head -40 $W/out/evtx_dump.xml"
  hdr "libevtx evtxexport -> text"
  r "evtxexport $W/sample.evtx > $W/out/evtxexport.txt 2> $W/evtxexport.err; echo rc=\$?; tail -5 $W/evtxexport.err; wc -l $W/out/evtxexport.txt; head -40 $W/out/evtxexport.txt"
  hdr "Plaso on the sample (parser winevtx) -> psort CSV"
  T=600 r "cd $W && log2timeline.py --status_view none --parsers winevtx --storage_file $W/out/sample.plaso $W/sample.evtx 2>&1 | tail -25; echo l2t-rc=\$?"
  T=300 r "cd $W && psort.py --status_view none -o l2tcsv -w $W/out/sample-l2t.csv $W/out/sample.plaso 2>&1 | tail -15; echo psort-rc=\$?; head -5 $W/out/sample-l2t.csv; wc -l $W/out/sample-l2t.csv"
  ;;

desktop)
  hdr "Desktop contents"
  r 'ls -la '$H'/Desktop'
  r 'for f in '$H'/Desktop/*.desktop; do [ -e "$f" ] || continue; echo "--- $f"; cat "$f"; gio info -a "metadata::*,access::*,standard::content-type" "$f"; done'
  r 'xdg-user-dir DESKTOP; cat '$H'/.config/user-dirs.dirs'
  hdr "GNOME / Nautilus / desktop icons"
  r 'gnome-shell --version; nautilus --version'
  r 'dpkg -l nautilus gnome-shell gnome-shell-extension-desktop-icons-ng gnome-shell-extension-ding gnome-shell-extension-ubuntu-dock gnome-shell-extension-appindicator nemo 2>&1 | grep -E "^(ii|un)"'
  r 'gnome-extensions list --enabled; echo ---; gnome-extensions list'
  r 'gsettings get org.gnome.shell enabled-extensions; gsettings get org.gnome.shell disabled-extensions'
  r 'gsettings list-recursively org.gnome.shell.extensions.ding 2>&1'
  r 'gsettings list-recursively org.gnome.nautilus.preferences 2>&1'
  r 'ls /usr/share/gnome-shell/extensions/ '$H'/.local/share/gnome-shell/extensions/ 2>&1'
  r 'grep -rn "metadata::trusted\|allow-launch\|executable" /usr/share/gnome-shell/extensions/ding@rastersoft.com/app/*.js 2>/dev/null | head -15'
  hdr "Trusted-launcher probe (creates and deletes /tmp files only; nothing clicked)"
  r 'f=/tmp/vmtest-probe.desktop; printf "[Desktop Entry]\nType=Application\nName=probe\nExec=true\n" > $f; chmod +x $f; gio info -a "metadata::trusted" $f; gio set $f metadata::trusted true; echo gio-set-rc=$?; gio info -a "metadata::trusted" $f; rm -f $f'
  hdr "VMware Tools desktop integration"
  r 'dpkg -l open-vm-tools open-vm-tools-desktop open-vm-tools-dev 2>&1 | grep -E "^(ii|un|rc)"'
  r 'vmware-toolbox-cmd -v; vmtoolsd --version 2>&1'
  r 'ps -eo pid,user,args | grep -E "[v]mtoolsd|[v]mware-user"'
  r 'ls /etc/xdg/autostart | grep -i vm; cat /etc/xdg/autostart/vmware-user.desktop 2>/dev/null'
  r 'ls /usr/lib/x86_64-linux-gnu/open-vm-tools/plugins/vmusr/ /usr/lib/x86_64-linux-gnu/open-vm-tools/plugins/vmsvc/ 2>&1'
  r 'vmware-toolbox-cmd timesync status 2>&1'
  hdr "Screen resolution"
  r 'for c in /sys/class/drm/card*-*; do echo "$c status=$(cat $c/status) enabled=$(cat $c/enabled 2>/dev/null)"; head -5 $c/modes 2>/dev/null; done'
  r 'cat '$H'/.config/monitors.xml 2>&1'
  r 'gdbus call --session --dest org.gnome.Mutter.DisplayConfig --object-path /org/gnome/Mutter/DisplayConfig --method org.gnome.Mutter.DisplayConfig.GetCurrentState 2>&1 | head -c 2500; echo'
  r 'lsmod | grep -iE "vmwgfx|drm"'
  hdr "Clipboard / drag-drop / shared folders"
  r 'ls /mnt/hgfs 2>&1; mount | grep -i hgfs; systemctl is-active run-vmblock\\x2dfuse.mount 2>&1'
  ;;

sift)
  hdr "SIFT identity"
  r 'command -v cast sift; ls -la $(command -v cast) $(command -v sift) 2>&1'
  r 'cast --version'
  r 'cast version 2>&1 | head; cast --help 2>&1 | head -30'
  r 'sift --version 2>&1 | head -5; sift version 2>&1 | head -5'
  r 'ls -la /etc/sift* /usr/share/sift* /opt/sift* /var/cache/sift* /etc/salt /srv/salt /var/cache/salt 2>&1 | head -60'
  r 'find / -xdev \( -path /proc -o -path /sys -o -path /snap -o -path /usr/share/doc -o -path "/home/*/.cache" \) -prune -o -maxdepth 4 \( -iname "*sift*version*" -o -iname "VERSION" -o -iname "*.sift" -o -iname "sift-*" -o -name "cast*" \) -print 2>/dev/null | head -50'
  r 'find /var/cache/salt /srv /var/cache/cast '$H'/.cache /root/.cache -maxdepth 4 -iname "*sift*" 2>/dev/null | head -40'
  r 'cat /etc/issue /etc/issue.net /etc/motd 2>&1; ls /etc/update-motd.d'
  r 'cat '$H'/.config/cast/* 2>/dev/null | head -30; ls -la '$H'/.config 2>&1'
  r 'ls -la /var/log | head -60; ls -la /var/log/sift* /var/log/cast* /var/log/salt* 2>&1'
  r 'grep -rl -i "sift" /etc/apt/sources.list.d/ 2>/dev/null; dpkg -l | grep -iE "sift|cast" | head'
  ;;

extra)
  hdr "Per-name command lookup (dash 'command -v' only reports the first name)"
  for b in evtxecmd EvtxECmd evtx_dump.py evtx_dump_json.py evtx_info.py evtxexport evtxinfo log2timeline.py psort.py pinfo.py psteal.py rip.pl mmls fls ewfmount ewfverify ewfinfo ewfexport ewfacquire hayabusa chainsaw vol vol.py vol3 volshell yara yarac tshark wireshark bulk_extractor regripper cast claude node rg; do
    p=$(command -v $b 2>/dev/null); printf '%-18s %s\n' "$b" "${p:-ABSENT}"
  done
  r 'dpkg -l | grep -iE " (yara|python3-yara|libyara[0-9]*|tshark|wireshark[a-z-]*|hayabusa|chainsaw|ripgrep|nodejs|python3-venv|python3.12-venv) " ; python3 -c "import yara; print(\"python yara\", yara.__version__)" 2>&1; /opt/volatility3/bin/python -c "import yara; print(\"vol3 venv yara\", yara.__version__)" 2>&1'
  r 'vol -h 2>&1 | grep -i -m2 "volatility"; head -1 /opt/volatility3/bin/vol; /opt/volatility3/bin/python -m pip show volatility3 2>&1 | head -2'
  r 'ewfinfo -V 2>&1 | head -1; ewfverify -h 2>&1 | head -3; dpkg -S "$(command -v ewfmount)" 2>&1'
  hdr "EvtxECmd wrapper with a path containing spaces"
  r 'mkdir -p "/tmp/evtx-test/dir with space" && cp /tmp/evtx-test/sample.evtx "/tmp/evtx-test/dir with space/Audit policy.evtx" && evtxecmd -f "/tmp/evtx-test/dir with space/Audit policy.evtx" --csv /tmp/evtx-test/out-space 2>&1 | tail -8; echo "wrapper-rc=$?"; ls /tmp/evtx-test/out-space 2>&1'
  r 'dotnet /opt/zimmermantools/EvtxeCmd/EvtxECmd.dll -f "/tmp/evtx-test/dir with space/Audit policy.evtx" --csv /tmp/evtx-test/out-space2 2>&1 | grep -E "Records included|Error|Processed"; echo "direct-dotnet-rc=$?"'
  hdr "Browser"
  r 'gio mime x-scheme-handler/https 2>&1; gio mime text/html 2>&1 | head -5'
  r 'ls /var/lib/snapd/desktop/applications/ 2>&1; ls /usr/share/applications | grep -iE "firefox|chrom" '
  T=90 r 'd=$(mktemp -d /tmp/chprobe.XXXX); s=$(date +%s); timeout 60 chromium --headless=new --no-first-run --disable-gpu --dump-dom about:blank > "$d/dom" 2> "$d/err"; rc=$?; echo "chromium headless rc=$rc seconds=$(( $(date +%s) - s ))"; head -c 300 "$d/dom"; echo; tail -5 "$d/err"'
  r 'chromium --version 2>&1 | tail -1'
  hdr "Shell behaviour a student terminal would have"
  r 'bash -ic "set -o | grep -E \"noclobber|posix\"; echo PATH=\$PATH; locale | head -1" 2>/dev/null'
  r 'bash -lc "echo login-PATH=\$PATH"'
  hdr "Case folders"
  r 'ls -ld /cases /mnt /mnt/* 2>&1; ls -la /cases 2>&1 | head'
  hdr "Claude installer endpoints (read-only HEAD/GET to /dev/null)"
  r 'for u in https://claude.ai/install.sh https://downloads.claude.ai/ https://storage.googleapis.com/ ; do curl -sS -o /dev/null -L --max-time 30 -A "curl/8.5.0" -w "%{http_code} %{url_effective} t=%{time_total}\n" "$u"; done'
  ;;

extra-root)
  hdr "SIFT / Salt version records"
  r 'cat /etc/salt/minion_id; echo; ls -la /var/cache/salt/minion; ls -la /var/log/salt'
  r 'find /var/cache/salt/minion -maxdepth 3 | head -40'
  r 'find /var/cache/salt/minion -maxdepth 6 -iname "*version*" 2>/dev/null | head; for f in $(find /var/cache/salt/minion -maxdepth 6 -iname "*version*" -type f 2>/dev/null | head -5); do echo "--- $f"; head -c 400 "$f"; echo; done'
  r 'ls -la /var/cache/sift/archives | head; ls /var/log/salt; tail -c 1500 /var/log/salt/minion 2>/dev/null; for f in /var/log/salt/*; do echo "--- $f"; head -5 "$f"; grep -m5 -iE "sift.*(version|release)|v20[0-9][0-9]" "$f"; done 2>/dev/null | head -60'
  r 'grep -rIl --exclude-dir=__pycache__ -iE "sift" /var/cache/salt/minion 2>/dev/null | head -20'
  hdr "Credential-like files (narrow; paths, sizes and modes only)"
  T=600 r 'find / -xdev \( -path /proc -o -path /sys -o -path /snap -o -path /usr/share -o -path /usr/lib -o -path "*/site-packages" -o -path "*/dist-packages" -o -path /var/lib/docker -o -path /etc/ssl \) -prune -o -type f \( -name ".env" -o -name "id_rsa" -o -name "id_ed25519" -o -name "id_ecdsa" -o -name "credentials" -o -name "credentials.json" -o -name ".credentials.json" -o -name ".netrc" -o -name ".git-credentials" -o -name "*.p12" -o -name "*.pfx" -o -name "*.kdbx" -o -name "hosts.yml" -o -name ".pgpass" -o -name "*.ovpn" \) -printf "%m %u %s %p\n" 2>/dev/null | head -40; echo find-done'
  r 'for f in /root/.config/* /root/.local/share/* /home/sansforensics/.local/share/*; do echo "$f"; done 2>/dev/null | head -40'
  r 'stat -c "%s %n" /home/sansforensics/.ssh/config /home/sansforensics/.ssh/authorized_keys /root/.ssh/authorized_keys 2>&1; grep -cE "^\s*Host" /home/sansforensics/.ssh/config'
  r 'ls -la /root/.docker /home/sansforensics/.docker 2>&1'
  r 'cat /etc/netplan/*.yaml | sed -E "s/(password|psk|key):.*/\1: [REDACTED]/I"'
  hdr "open-vm-tools configuration"
  r 'cat /etc/vmware-tools/tools.conf 2>&1 | grep -vE "^\s*#|^\s*$" | head -30; ls /etc/vmware-tools'
  ;;

*)
  echo "unknown section: $1" >&2; exit 2 ;;
esac
exit 0
