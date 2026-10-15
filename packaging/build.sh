#!/bin/sh
# Builds dist/opensonar_<version>_amd64.deb and dist/opensonar-<version>-x86_64.AppImage.
# Python 3.12 (python-build-standalone, relocatable) and every dependency are bundled: the user needs
# no venv and no pip. Usage: packaging/build.sh   (needs curl, dpkg-deb, fakeroot; ~1 GB of disk)
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PKG="$ROOT/packaging"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
CACHE="$BUILD/cache"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20241016/cpython-3.12.7+20241016-x86_64-unknown-linux-gnu-install_only.tar.gz"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml")
# runtime packages; PySide6-Essentials instead of PySide6 (no WebEngine/3D/Qt Quick add-ons: −500 MB)
DEPS="numpy pillow pyzmq PySide6-Essentials pyqtgraph matplotlib imageio-ffmpeg"

# isolated from the build machine's Python (ROS, conda, user site-packages)
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV CONDA_PREFIX
export PYTHONNOUSERSITE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
mkdir -p "$CACHE" "$DIST"
[ -f "$CACHE/python.tar.gz" ] || curl -fsSL -o "$CACHE/python.tar.gz" "$PY_URL"
[ -x "$CACHE/appimagetool" ] || { curl -fsSL -o "$CACHE/appimagetool" "$APPIMAGETOOL_URL"; chmod +x "$CACHE/appimagetool"; }

# --- 1. the bundled Python with opensonar and its dependencies -------------------------------------------
STAGE="$BUILD/stage"
rm -rf "$STAGE" && mkdir -p "$STAGE"
tar -xzf "$CACHE/python.tar.gz" -C "$STAGE"                      # → $STAGE/python
PY="$STAGE/python/bin/python3"
"$PY" -m pip install -q --no-warn-script-location --upgrade pip
"$PY" -m pip install -q --no-warn-script-location $DEPS
"$PY" -m pip install -q --no-warn-script-location --no-deps "$ROOT"
# trim: tests, caches, headers, Qt tools the GUI does not use
find "$STAGE/python" -depth \( -name __pycache__ -o -name tests -o -name test \) -type d -exec rm -rf {} +
rm -rf "$STAGE/python/include" "$STAGE/python/share" "$STAGE/python/lib/python3.12/idlelib" \
       "$STAGE/python/lib/python3.12/tkinter" "$STAGE/python/lib/python3.12/ensurepip" \
       "$STAGE/python/lib/python3.12"/config-3.12-*
SITE="$STAGE/python/lib/python3.12/site-packages"
rm -rf "$SITE/PySide6/Qt/translations" "$SITE/PySide6/designer" "$SITE/PySide6/linguist" \
       "$SITE/PySide6/assistant" "$SITE/PySide6/lupdate" "$SITE/PySide6/lrelease" \
       "$SITE/PySide6/qmllint" "$SITE/PySide6/qmlls" "$SITE/PySide6/qmlformat" "$SITE/pip" "$SITE"/pip-*
"$PY" -m compileall -q -j 0 "$STAGE/python/lib/python3.12" >/dev/null || true

# --- 2. icons ------------------------------------------------------------------------------------------
ICONS="$BUILD/icons"
rm -rf "$ICONS" && mkdir -p "$ICONS"
"$PY" "$PKG/make_icon.py" "$ICONS" 48 64 128 256 512

# --- 3. .deb -------------------------------------------------------------------------------------------
DEB="$BUILD/deb"
rm -rf "$DEB"
mkdir -p "$DEB/DEBIAN" "$DEB/opt/opensonar/bin" "$DEB/usr/bin" "$DEB/usr/share/applications" \
         "$DEB/lib/systemd/system" "$DEB/etc/default" "$DEB/usr/share/polkit-1/rules.d" \
         "$DEB/var/lib/polkit-1/localauthority/10-vendor.d" "$DEB/usr/share/doc/opensonar" \
         "$DEB/usr/share/icons/hicolor/scalable/apps"
cp -a "$STAGE/python" "$DEB/opt/opensonar/python"
cat > "$DEB/opt/opensonar/bin/opensonar" <<'EOF'
#!/bin/sh
# opensonar launcher: the bundled Python, isolated from the user's environment (-I) and writing no bytecode (-B)
exec /opt/opensonar/python/bin/python3 -I -B -m opensonar "$@"
EOF
chmod 755 "$DEB/opt/opensonar/bin/opensonar"
ln -s /opt/opensonar/bin/opensonar "$DEB/usr/bin/opensonar"
cp "$PKG/opensonar.desktop" "$DEB/usr/share/applications/"
for n in 48 64 128 256 512; do
    mkdir -p "$DEB/usr/share/icons/hicolor/${n}x${n}/apps"
    cp "$ICONS/opensonar_$n.png" "$DEB/usr/share/icons/hicolor/${n}x${n}/apps/opensonar.png"
done
cp "$PKG/opensonar.svg" "$DEB/usr/share/icons/hicolor/scalable/apps/opensonar.svg"
cp "$PKG/opensonar-server.service" "$DEB/lib/systemd/system/"
cp "$PKG/opensonar-server.default" "$DEB/etc/default/opensonar-server"
cp "$PKG/50-opensonar.rules" "$DEB/usr/share/polkit-1/rules.d/"
cp "$PKG/50-opensonar.pkla" "$DEB/var/lib/polkit-1/localauthority/10-vendor.d/"
cp "$ROOT/LICENSE" "$DEB/usr/share/doc/opensonar/copyright"
cp "$ROOT/README.md" "$ROOT/docs/INSTALL.md" "$ROOT/docs/GUI.md" "$ROOT/docs/SERVER.md" "$ROOT/docs/SYNC.md" \
   "$DEB/usr/share/doc/opensonar/" 2>/dev/null || true
cp "$PKG/deb/postinst" "$PKG/deb/prerm" "$PKG/deb/postrm" "$DEB/DEBIAN/"
echo "/etc/default/opensonar-server" > "$DEB/DEBIAN/conffiles"
SIZE=$(du -sk "$DEB" | cut -f1)
cat > "$DEB/DEBIAN/control" <<EOF
Package: opensonar
Version: $VERSION
Section: science
Priority: optional
Architecture: amd64
Installed-Size: $SIZE
Depends: libc6 (>= 2.35), libegl1, libgl1, libglib2.0-0, libfontconfig1, libfreetype6, libdbus-1-3,
 libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1,
 libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libxcb-xkb1, libx11-xcb1,
 iputils-ping
Recommends: network-manager
Maintainer: Henrique Reichow <reichowhenrique@gmail.com>
Homepage: https://github.com/HenriqueReichow/OpenSonar
Description: open SDK and viewer for P900 and M900 imaging sonars
 Reads and writes .son files, renders the XY/R-Theta images,
 operates the sonar live over the network with its own driver, records in several
 formats and publishes the pings over ZeroMQ. Includes a graphical interface, the
 opensonar command and an optional systemd service. Python and every dependency are
 bundled in /opt/opensonar.
EOF
fakeroot dpkg-deb --build -Zxz "$DEB" "$DIST/opensonar_${VERSION}_amd64.deb" >/dev/null
echo "built $DIST/opensonar_${VERSION}_amd64.deb"

# --- 4. AppImage ---------------------------------------------------------------------------------------
APPDIR="$BUILD/AppDir"
rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr"
cp -a "$STAGE/python" "$APPDIR/usr/python"
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
# opensonar AppImage: no arguments = the GUI; otherwise the opensonar command (server, ctl, export, …)
HERE=$(dirname "$(readlink -f "$0")")
[ $# -eq 0 ] && set -- gui
exec "$HERE/usr/python/bin/python3" -I -B -m opensonar "$@"
EOF
chmod 755 "$APPDIR/AppRun"
sed 's/^Exec=opensonar gui/Exec=AppRun gui/' "$PKG/opensonar.desktop" > "$APPDIR/opensonar.desktop"
cp "$ICONS/opensonar_256.png" "$APPDIR/opensonar.png"
ln -sf opensonar.png "$APPDIR/.DirIcon"
ARCH=x86_64 "$CACHE/appimagetool" --appimage-extract-and-run --no-appstream "$APPDIR" \
    "$DIST/opensonar-${VERSION}-x86_64.AppImage" >/dev/null 2>&1
echo "built $DIST/opensonar-${VERSION}-x86_64.AppImage"
ls -la "$DIST"
