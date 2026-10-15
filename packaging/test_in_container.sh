#!/bin/sh
# Runs inside a clean Ubuntu container (packaging/test_packages.sh): installs the .deb, exercises the
# commands, the service and the GUI (offscreen), validates the desktop entry and the systemd unit,
# runs the AppImage, then uninstalls and checks that nothing is left.
set -eu
export DEBIAN_FRONTEND=noninteractive
step() { echo; echo "=== $*"; }
apt-get update -qq
step "install the .deb (dependencies from the Ubuntu archive)"
apt-get install -y -qq /pkg/opensonar_*_amd64.deb desktop-file-utils systemd >/dev/null
. /etc/os-release; echo "$PRETTY_NAME"
command -v opensonar
opensonar --help | head -3
step "export"
opensonar export /data/sample.son /tmp/out --ping 0 --format npy --format png
ls /tmp/out
step "service + ctl"
opensonar server --rep-port 4545 --pub-port 4546 >/tmp/server.log 2>&1 &
sleep 2
opensonar ctl connect /data/sample.son | head -4
opensonar ctl start 0 image >/dev/null
sleep 1
opensonar ctl status | grep -E '"published"|"error"'
opensonar ctl quit >/dev/null
wait
step "GUI (offscreen): window, file playback, colour mapping"
QT_QPA_PLATFORM=offscreen /opt/opensonar/python/bin/python3 -I -B - <<'PY'
import time
from PySide6 import QtWidgets, QtCore
app = QtWidgets.QApplication([])
from opensonar.gui import Service, build
svc = Service()
w = build(svc, QtCore.QSettings("/tmp/gui.ini", QtCore.QSettings.IniFormat))
w.show()
w.connect_to("/data/sample.son")
w.start()
t = time.time()
while w.views[0].last is None and time.time() - t < 30:
    app.processEvents(); time.sleep(0.01)
assert w.views[0].last is not None, "no image"
print("GUI ok: image", w.views[0].image.image.shape, "service owned:", svc.owned)
p = svc.process
w.close()
time.sleep(0.5)
assert p.poll() is not None, "service still running"
print("service stopped with the window")
PY
step "desktop entry, icon, systemd unit, polkit, network profile"
desktop-file-validate /usr/share/applications/opensonar.desktop && echo "desktop entry valid"
ls /usr/share/icons/hicolor/256x256/apps/opensonar.png /usr/share/icons/hicolor/scalable/apps/opensonar.svg
systemd-analyze verify /lib/systemd/system/opensonar-server.service && echo "systemd unit valid"
echo "service enabled? $(systemctl is-enabled opensonar-server.service 2>/dev/null || true)"
ls /usr/share/polkit-1/rules.d/50-opensonar.rules /var/lib/polkit-1/localauthority/10-vendor.d/50-opensonar.pkla
step "AppImage (extracted, no FUSE in a container)"
cp /pkg/opensonar-*-x86_64.AppImage /tmp/opensonar.AppImage
/tmp/opensonar.AppImage --appimage-extract-and-run export /data/sample.son /tmp/out2 --ping 1 >/dev/null
ls /tmp/out2
step "uninstall (purge)"
apt-get purge -y -qq opensonar >/dev/null
for f in /opt/opensonar /usr/bin/opensonar /usr/share/applications/opensonar.desktop /lib/systemd/system/opensonar-server.service \
         /etc/default/opensonar-server /usr/share/polkit-1/rules.d/50-opensonar.rules /usr/share/icons/hicolor/256x256/apps/opensonar.png; do
    if [ -e "$f" ]; then echo "LEFT BEHIND: $f"; exit 1; fi
done
echo "nothing left behind"
echo; echo "ALL OK"
