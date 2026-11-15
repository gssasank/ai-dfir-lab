#!/bin/bash
# Watches the desktop for Ubuntu's updater windows for SECONDS seconds and
# appends one line every 10 s to LOG:
#   <UTC time> procs=<updater processes> windows=<top-level windows of updater apps>
# Windows come from the accessibility bus (AT-SPI), which lists every
# application's frames on this GNOME/Wayland desktop; a headless VM's screenshot
# is blank, so this is how a dialog is seen. Run in the desktop session
# (systemd-run --user gives it the session environment).
#
#   watch-updaters.sh SECONDS LOG
set -u
end=$((SECONDS + $1))
log=$2
while [ $SECONDS -lt "$end" ]; do
    procs=$(pgrep -a -f 'update-manager|check-new-release|do-release-upgrade|update-notifier|software-properties' |
            grep -v -E 'watch-updaters|pgrep' | awk '{print $2"/"$3}' | tr '\n' ' ')
    wins=$(python3 - <<'EOF' 2>/dev/null
import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi
d = Atspi.get_desktop(0)
out = []
for i in range(d.get_child_count()):
    app = d.get_child_at_index(i)
    if app is None:
        continue
    name = app.get_name() or ''
    if not any(k in name for k in ('update', 'release', 'software')):
        continue
    for j in range(app.get_child_count()):
        w = app.get_child_at_index(j)
        if w is not None:
            out.append('%s:%s' % (name, w.get_name()))
print(';'.join(out))
EOF
)
    echo "$(date -u +%H:%M:%S) procs=[${procs% }] windows=[$wins]" >> "$log"
    sleep 10
done
echo "$(date -u +%H:%M:%S) END" >> "$log"
