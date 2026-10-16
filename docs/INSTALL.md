# Installing opensonar

Two packages, both with **Python 3.12 and every dependency bundled**: no venv, no pip.

| Package | For | Size |
|---|---|---|
| `opensonar_<version>_amd64.deb` | Ubuntu 22.04 / 24.04 (and derivatives), installed system-wide | ~137 MB download, ~690 MB installed in `/opt/opensonar` |
| `opensonar-<version>-x86_64.AppImage` | any recent x86-64 Linux, no installation | ~193 MB, one file |

For development, the `pip install -e ".[gui,viewer]"` route of the README is still the one to use.

**Getting the packages**, either way:
1. Download them from the [Releases page](https://github.com/HenriqueReichow/OpenSonar/releases).
2. Or build them locally (~5 min, needs internet; see [section 3](#3-building-the-packages)):
   ```sh
   sudo apt install fakeroot curl
   sh packaging/build.sh                 # → dist/opensonar_4.1.0_amd64.deb and dist/opensonar-4.1.0-x86_64.AppImage
   sudo apt install ./dist/opensonar_4.1.0_amd64.deb
   ```
   With a local build, the AppImage commands below use `./dist/opensonar-4.1.0-x86_64.AppImage`.

The commands below assume the downloaded file is in the current folder.

## 1. The .deb (recommended)

```sh
sudo apt install ./opensonar_4.1.0_amd64.deb
```
This is the **only step that asks for a password**. It installs:

| What | Where |
|---|---|
| opensonar with its own Python | `/opt/opensonar` |
| the `opensonar` command (`opensonar gui`, `server`, `ctl`, `export`, `live`, `discover`, `network`) | `/usr/bin/opensonar` |
| menu entry "opensonar" and icon | `/usr/share/applications/opensonar.desktop`, `/usr/share/icons/hicolor/*/apps/opensonar.*` |
| network profile **opensonar-net** (192.168.1.10/24 for the P900/M900 and 169.254.10.10/16 for the `.aris` family, on the first wired card; no gateway, no default route, no IPv6, not automatic; an existing profile gets the second address on upgrade) | NetworkManager, created by the installer |
| ufw rules for UDP 56124 (`.aris` beacons) and 56125 (`.aris` frames), only if ufw is active; removed on uninstall | ufw |
| polkit rule, so the profile can be turned on without a password | `/usr/share/polkit-1/rules.d/50-opensonar.rules` (24.04), `/var/lib/polkit-1/localauthority/10-vendor.d/50-opensonar.pkla` (22.04) |
| optional service `opensonar-server.service`, **off by default** | `/lib/systemd/system/`, settings in `/etc/default/opensonar-server` |
| documentation | `/usr/share/doc/opensonar/` |

The dependencies (Qt's X11/OpenGL libraries, `iputils-ping`) come from the Ubuntu archive, which is why
`apt install ./…` is used and not `dpkg -i`.

### Using it
1. Open **opensonar** from the applications menu (or run `opensonar gui`).
2. Plug the sonar into the wired card and press **Configure sonar network**. It turns the
   `opensonar-net` profile on (no password) and checks that 192.168.1.45 answers. The same from a
   terminal: `opensonar network`. To return to the normal network, pick the usual wired connection in
   the system's network menu (or `nmcli connection up "Wired connection 1"`).
3. **Discover sonars** → **Connect to selected** → **Start** ([`GUI.md`](GUI.md)).

Notes:
- The profile is bound to the wired card that existed at install time. If the PC has several, or the
  card changes, delete it (`nmcli connection delete opensonar-net`) and press the button again: it
  recreates it for the first wired card, as a profile of your user (no password either).
- Polkit cannot single out one NetworkManager profile, so the rule allows users of a **local, active
  session** to bring any profile up or down. That is already Ubuntu's default for desktop users: the
  rule only makes it explicit.
- The P900/M900 factory IP is 192.168.1.45; `.aris` sonars take a link-local 169.254.x.x address when
  there is no DHCP server, which the second address of the profile reaches. If your subnet differs,
  change the addresses with
  `nmcli connection modify opensonar-net ipv4.addresses 192.168.1.10/24,169.254.10.10/16` (your values).

### The service on the ROV computer (no GUI)
```sh
sudoedit /etc/default/opensonar-server        # e.g. OPENSONAR_SOURCE=192.168.1.45, OPENSONAR_ARGS="--bind 0.0.0.0 --head 0"
sudo systemctl enable --now opensonar-server
systemctl status opensonar-server
opensonar ctl status                          # the same commands as ever (docs/SERVER.md)
```
It runs as an unprivileged dynamic user, with its working directory in `/var/lib/opensonar`, so relative
recording paths land there. It restarts if it crashes. A GUI on the same PC finds the running service
and uses it instead of starting its own, so it keeps running when the GUI closes. `--bind 0.0.0.0`
exposes it to the vehicle network; it has no authentication, so use it on a trusted network only.
Time synchronisation with the ROV: [`SYNC.md`](SYNC.md).

### Updating and uninstalling
```sh
sudo apt install ./opensonar_<new version>_amd64.deb   # update: keeps the network profile (adds the second address) and /etc/default/opensonar-server
sudo apt install --reinstall ./opensonar_<same version>_amd64.deb   # a rebuilt package with the same version
sudo apt remove opensonar                              # removes the program, the menu entry, the service, the network profile and the ufw rules
sudo apt purge opensonar                               # also removes /etc/default/opensonar-server and /var/lib/opensonar
```
Nothing is left in `/opt`, `/usr`, `/lib/systemd` or NetworkManager. Recordings you made elsewhere are
never touched, and neither are the GUI settings in `~/.config/opensonar/` (delete that folder to reset
them).

## 2. The AppImage
```sh
chmod +x opensonar-4.1.0-x86_64.AppImage
./opensonar-4.1.0-x86_64.AppImage                 # the GUI (also by double-clicking it)
./opensonar-4.1.0-x86_64.AppImage server …        # any opensonar command: server, ctl, export, network…
```
- It needs FUSE 2: `sudo apt install libfuse2` (22.04) or `libfuse2t64` (24.04). Without it:
  `./opensonar…AppImage --appimage-extract-and-run`.
- No installation, so no menu entry, no polkit rule and no service. **Configure sonar network**
  creates the `opensonar-net` profile **for your user** the first time (NetworkManager allows that
  without a password in a local session) and activates it.
- For a terminal command: `ln -s "$PWD/opensonar-4.1.0-x86_64.AppImage" ~/.local/bin/opensonar`.
- Uninstalling: delete the file, then `nmcli connection delete opensonar-net` if it was created.

## 3. Building the packages
```sh
packaging/build.sh                    # → dist/opensonar_<version>_amd64.deb and dist/opensonar-<version>-x86_64.AppImage
packaging/test_packages.sh samples/exp1.son            # clean ubuntu:22.04 and ubuntu:24.04 containers
```
`build.sh` downloads Python 3.12 (python-build-standalone 20241016) and `appimagetool` into
`build/cache/`, installs opensonar and its dependencies into that Python, and assembles both packages. It
takes about 1 minute. It uses `PySide6-Essentials` (QtCore/Gui/Widgets/OpenGL) instead of the full
PySide6, which saves about 500 MB. The Python is isolated from the build machine's (ROS, conda).

`test_packages.sh` runs `test_in_container.sh` in each image:
- installs the .deb with `apt`;
- runs `opensonar export` (NPY + PNG), `opensonar server` + `opensonar ctl` on a `.son`;
- opens the GUI offscreen (the service starts with the window, a ping is shown, and the service stops
  when the window closes);
- validates the desktop entry (`desktop-file-validate`) and the systemd unit (`systemd-analyze verify`),
  and checks that the service is disabled;
- runs the AppImage;
- purges, and checks that nothing is left.

### Test results (2026-10-14)
| Test | Ubuntu 22.04 | Ubuntu 24.04 |
|---|---|---|
| install with apt, `opensonar` on the PATH | ✅ | ✅ |
| export, server + ctl, GUI offscreen (service started and stopped with the window) | ✅ | ✅ |
| desktop entry valid, icons, systemd unit valid and disabled, polkit files | ✅ | ✅ |
| AppImage | ✅ | ✅ |
| purge leaves nothing behind | ✅ | ✅ |
| with systemd + NetworkManager (privileged container): profile created by the installer (manual 192.168.1.10/24 + 169.254.10.10/16, never-default, not autoconnect), service enabled and serving, upgrade keeps profile and settings, purge removes the profile | — | ✅ |

### On the real PC (2026-10-14, Ubuntu 22.04 desktop, M900/2250-130 on `enp4s0`)
The .deb installed with `sudo apt install`, then:

| Step | Result |
|---|---|
| "opensonar" in the applications menu, with its icon | ✅ |
| the GUI opens with one click (no terminal), starting the service by itself | ✅ |
| **Configure sonar network** activates `opensonar-net` **without a password** (polkit) | ✅ |
| the sonar answers at 192.168.1.45 | ✅ |
| discover, connect, show the fan and record | ✅ |
