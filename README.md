# OpenSonar

**Open software for imaging sonars on Linux (P900/M900 live and `.son`; `.aris` live and recorded;
`.ddf` recorded): a graphical interface to operate the sonar and record its data, a service that shares
the pings with other programs (ROV, ROS 2, C++, Unity, a digital twin), and a Python SDK for recordings
and images. No Windows and no closed library.**

## What it is

Multibeam imaging sonars are used on ROVs and AUVs: each ping becomes an acoustic "photo" of what lies
ahead, a fan of tens of metres, even in murky water. They usually come with closed software: a viewer
for Windows only, and a binary SDK. That makes it hard to put the sonar
on a Linux vehicle computer, record it alongside the other sensors, or process its data in your own
code.

OpenSonar is an open alternative, written from scratch, with its own specification of the file format,
the image algorithm and the network protocol (`docs/`). The goal is a generic tool for the main imaging
sonars, aimed at 3D reconstruction. Two families are supported: sonars that send the raw signal and
are beamformed here (P900/M900), and sonars that form the beams in an acoustic lens and send ready
frames (`.aris`, `.ddf`). Both end up in one common `Frame` (intensities on a range × beam angle grid),
which the GUI, the service, the recording and ROS 2 share. It was developed as an undergraduate thesis
(TCC) and is MIT-licensed.

It is organised in layers, and each one can be used on its own:

```
 ┌───────────────┐  ┌───────────────┐  ┌────────────────────────┐
 │  opensonar gui  │  │  opensonar ctl  │  │ your program / ROS 2 / │   clients
 │ (PySide6 GUI) │  │ (commands)    │  │ C++ / Unity (ZeroMQ)   │
 └───────┬───────┘  └───────┬───────┘  └───────────┬────────────┘
         └──────── commands (REQ/REP) + pings (PUB/SUB) ┘
                            │
                 ┌──────────┴──────────┐
                 │   opensonar server    │   owns the sonar, records, publishes every ping (UTC)
                 └──────────┬──────────┘
                 ┌──────────┴──────────┐
                 │  Python SDK         │   drivers, readers (.son/.aris/.ddf), .son/.aris writers,
                 └──────────┬──────────┘   XY/R-Theta images, common Frame
                 sonar, or .son / .aris / .ddf file
```

## What it does

| | |
|---|---|
| **Operate the sonar** | discover sonars on the network (free or busy), connect, pick heads (several side by side), set range, gain and TVG, start/stop; one click sets up the PC's network for both families |
| **See** | the fan in metres with range rings and bearings (Copper, gamma, Auto Intensity, Sensitivity/Intensity); cursor range/bearing, distance ruler, zoom |
| **Record** | any combination of the raw recording (`.son`, or `.aris` for those sonars), MP4 video, NPY, NPZ, 16-bit PNG/TIFF per ping with an index of UTC times; event marks with UTC time and text for syncing with the ROV |
| **Play back** | `.son`, `.aris` and `.ddf` files with a time bar, speed, step and go-to-ping; export a range of pings in several formats |
| **Share** | the service publishes every ping (raw signal + optional image, corrected UTC time) to any number of programs over ZeroMQ, and takes commands ([`docs/SERVER.md`](docs/SERVER.md)) |
| **Program** | a Python API for files and live sonars: `opensonar.open(...)`, XY/R-Theta images, the common `Frame`, `.son` writing ([`docs/API.md`](docs/API.md)) |
| **ROS 2** | `ros2/`: a Humble node publishing the fan, the raw pings or the beam frames, head info, diagnostics, and optionally the ping in a simulator's imaging-sonar message ([`ros2/README.md`](ros2/README.md)) |
| **Install** | `.deb` for Ubuntu 22.04/24.04 and an AppImage, with Python bundled: menu entry, `opensonar` command, optional systemd service ([`docs/INSTALL.md`](docs/INSTALL.md)) |
| **Test without hardware** | emulated sonars that speak each protocol and replay a `.son` or a `.aris` |

**Quick start:** install the package, open **opensonar** from the menu, press **Configure sonar network**,
**Discover sonars**, **Connect to selected** and **Start**. Details below.

The P900/M900 images were validated against reference images of the same recordings: correlation
above 0.999.

---

## Supported sonars

| Family | Live | Recordings | Checked against |
|---|---|---|---|
| **P900 / M900** (raw signal) | ✅ native driver | `.son` read and write | reference images (> 0.999) and a real M900/2250-130 |
| **`.aris`** (lens, beamformed) | ✅ driver ([`docs/ARIS_LIVE.md`](docs/ARIS_LIVE.md)) | `.aris` read and write ([`docs/ARIS_FORMAT.md`](docs/ARIS_FORMAT.md)) | the published sample recording; live only against the emulator so far |
| **`.ddf`** (lens, beamformed) | — | `.ddf` read ([`docs/DDF_FORMAT.md`](docs/DDF_FORMAT.md)) | public recordings, version 3, 96 beams |

Independent project, not affiliated with any manufacturer. It reads file formats and protocols that are
publicly documented or were analysed for interoperability.

P900/M900 models checked:

| Model | `.son` revision | Image (correlation with the reference) |
|---|---|---|
| **P900-130** | 13, 14 | > 0.999 |
| **P900-45** | 10 | > 0.997 |
| **M900-130** | 13 | > 0.999 |
| **M900/2250-130 (dual-frequency head)** | live, rev 14 | 0.999 |

### Live
- The network driver was validated against the emulator, with images **identical** to the source `.son`
  for P900-130, M900-130 and MB2250-45, with and without signal compression.
- With a real M900/2250-130 (6 heads), the native driver discovers, connects, sets range/gain/TVG,
  pings and records at the full rate (~19 pings/s with two heads), also through the service and the
  GUI. Validation of the images against real echoes (a test tank) is still pending (`docs/LIVE.md`).
- `.aris` family: the service is the only client of the sonar (as the protocol requires), keeps it
  alive with a PING every second, reconnects by itself and refuses a sonar that another program has.
  Not yet tried on a real sonar.

---

## Installation

### Packages (no Python setup needed)
Download the .deb or the AppImage from the
[Releases page](https://github.com/HenriqueReichow/OpenSonar/releases), then:
```sh
sudo apt install ./opensonar_4.1.0_amd64.deb      # Ubuntu 22.04 / 24.04: menu entry, `opensonar` command,
                                                 # sonar network profile, optional systemd service
./opensonar-4.1.0-x86_64.AppImage                  # or: any Linux, no installation
```
Or build them locally (~5 min, needs internet); they land in `dist/`:
```sh
sudo apt install fakeroot curl
sh packaging/build.sh
sudo apt install ./dist/opensonar_4.1.0_amd64.deb
./dist/opensonar-4.1.0-x86_64.AppImage             # or the AppImage
```
Python and every dependency are bundled. The .deb also creates the **opensonar-net** network profile
(192.168.1.10/24 + 169.254.10.10/16 on the wired card), so the GUI's **Configure sonar network** button sets up the
network with one click and no password. Details, the service, uninstalling and building the packages:
[`docs/INSTALL.md`](docs/INSTALL.md).

### From source (development)

Requires Python 3.9 or newer (Linux; the network part was tested on Ubuntu).

```sh
git clone https://github.com/HenriqueReichow/OpenSonar.git
cd OpenSonar
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[gui,viewer]"     # everything: graphical interface, service and simple viewer
```

This installs the `opensonar` command. Pick only what you need:

| Extra | Installs | For |
|---|---|---|
| (none) `pip install -e .` | numpy, pillow | reading/writing `.son`, images, export, live acquisition without a window |
| `[viewer]` | matplotlib | the simple window of `opensonar live` |
| `[server]` | pyzmq | `opensonar server` / `opensonar ctl` / `opensonar.client` |
| `[gui]` | PySide6, pyqtgraph, pyzmq, imageio-ffmpeg | `opensonar gui` (with MP4 video) |
| `[video]` | imageio-ffmpeg (bundles ffmpeg) | `opensonar export --format mp4` |

---

## Using the graphical interface (recommended)

### 1. Network (once per PC)
With the .deb or the AppImage, press **Configure sonar network** in the window (or run
`opensonar network`), and that is all: the profile has 192.168.1.10/24 (P900/M900) and 169.254.10.10/16
(`.aris` family, link-local) on the same card, so one cable serves both. With ufw active, the .deb also
allows UDP 56124 and 56125 (`.aris` beacons and frames). Without the packages, by hand: a P900/M900 has a fixed IP. The factory default is **192.168.1.45**, so the PC's wired card must be in the
same subnet, for example 192.168.1.10/24:
```sh
nmcli device disconnect enp4s0                 # your wired card: see `ip -br link`
sudo ip addr add 192.168.1.10/24 dev enp4s0
sudo ip link set enp4s0 up
ping -c 3 192.168.1.45                         # the sonar answers about 40 s after power-on
```
If the Wi-Fi uses the same subnet, turn it off. If the cable is unplugged and plugged back in,
NetworkManager may take the card over again: repeat these commands.

### 2. Start
```sh
opensonar gui
```
The window starts the opensonar service by itself and stops it when you close the window (if a service
was already running, the window uses it and leaves it running).

### 3. Connect
- **Discover sonars**: lists the sonars on the network with model, serial, IP and whether each one is
  free or busy (taken by another program). Select one and press **Connect to selected** (or double-click it).
- Or type the IP (`192.168.1.45`, or `ip:port`), choose the type (**P900 / M900** or **.aris sonar**)
  and press **Connect**. The last IP and type are remembered.
- Or **Open recording…** to play a `.son`, `.aris` or `.ddf` back, at the pace it was recorded.
- With a `.aris` sonar, the status bar warns when no frame arrives within ~5 s of connecting (the sonar
  may have refused the settings) and when the connection dropped and came back.

### 4. Choose heads
The **Heads** list shows each head with its model and frequency (a dual-frequency M900/2250 has six:
M2250-130/90/45 and M900-130/90/45). Tick the ones to show: each gets its own panel, side by side.
The selection can change while pinging.

### 5. Acquire
In **Acquisition**, pick the head to control, then set the **range** (slider, or start/stop in metres),
**Gain** and **TVG**, and press **Start** / **Stop**. With several heads ticked, the sonar pings them in
turn. Range, gain and TVG only apply to a live sonar, not to a file (`.aris` sonars have no TVG).

### 6. Adjust the display
The display ([`docs/DISPLAY.md`](docs/DISPLAY.md)) only changes what you
see, never the data:
- **Colormap**: our own Copper (default), Grayscale, Jet and Viridis; **.cmap folder…** loads `.cmap`
  files you already have.
- **Auto Intensity** (on): adjusts the brightness to every image. Turn it off to use:
  - **Sensitivity**: raise it to hide the background noise;
  - **Intensity**: raise it for a brighter image;
  - **Gamma**: 0.5 by default; lower it to bring out weak echoes.
- **Smoothing (3×3)** and **Range rings and bearings** (rings in metres, rays every 15°).

### 7. Record, mark, measure, play back
- **● Record**: choose a folder, a name and **any combination of formats**: raw (`.son`, or `.aris`), MP4 video (as on
  screen), NPY, NPZ, PNG and TIFF 16 bit (one file per ping + `index.csv` with UTC time and geometry).
  The service records every ping, and the status bar shows time, pings and size. **⚑ Mark** (key M) adds an event mark with UTC time and text, saved next to the
  recording (`.events.csv`) and published for other programs.
- Mouse over the fan: x/y, range and bearing in the status bar. **📏 Ruler** (key R): distance between
  two clicks. **Save screen (PNG)** / **Save image (TIFF 16 bit)**.
- A recording opened in the window gets a playback bar: play/pause, previous/next ping, time bar, speed,
  go to ping; **Export pings…** saves a range in several formats at once (MP4, NPY, NPZ, PNG, TIFF).
- The status bar also shows pings/s, lost pings, the UTC time of the last ping and the sonar clock
  offset. Settings (IP, heads, display, window) are remembered.

Tips: the transducer face must be under water, otherwise the image is uniform noise. In a small
container the walls dominate Auto Intensity, so turn it off and lower the Intensity. More in
[`docs/GUI.md`](docs/GUI.md).

---

## Using the command line

### Recordings (`.son`, `.aris`, `.ddf`)

```sh
# every ping as a fan (16-bit PNG)
opensonar export recording.son out/

# ping 0 in R-Theta, as TIFF
opensonar export recording.son out/ --type rtheta --format tiff --ping 0

# pings 10 and 20 in NPZ, with time, ranges and resolution
opensonar export recording.son out/ --format npz --ping 10 --ping 20

# every ping as a NumPy array (.npy) and as a colour video (.mp4)
opensonar export recording.son out/ --format npy --format mp4

# play the recording back in a simple window, as if it were live
opensonar live recording.son

# the same commands take .aris and .ddf files
opensonar export sample.aris out/ --format npz
```

The PNG/TIFF files are 16 bit: the value is the echo amplitude, and the image looks dark in a regular
viewer. Use contrast or a log scale (Python example below).

### Live sonar without the GUI

With the network set up as in step 1 above:

```sh
opensonar discover                                     # lists the sonars on the network

opensonar live 192.168.1.45                            # simple window with the live fan
opensonar live 192.168.1.45 --range 20 --gain 8        # range up to 20 m and gain 8
opensonar live 192.168.1.45 --head 3 --record session.son        # another head, recording everything
opensonar live 192.168.1.45 --no-window --pings 100 --npz pings/   # no screen, one NPZ per ping
```

Window keys: **space** pause · **↑/↓** range ±20 % · **c** colormap · **l** log/linear scale ·
**g** grid · **s** save the fan (16-bit PNG) · **q** quit.

Acquisition and recording run in a separate process. The window always shows the latest ping, and the
`.son` receives every one. The full field checklist, with network setup, traffic capture and what to
check, is in [`docs/LIVE.md`](docs/LIVE.md).

### Service for other programs

```sh
pip install -e ".[server]"
opensonar server 192.168.1.45 --head 0 --record session.son &   # records every ping, publishes them
python examples/subscriber.py --head 0                        # any number of subscribers
opensonar ctl range 0 1 20                                      # commands from anywhere
```
For a `.aris` sonar, connect with `opensonar ctl connect` and the `family` option, or from the GUI.
Protocol (JSON commands + multipart pings, for any language): [`docs/SERVER.md`](docs/SERVER.md).

---

### Without a sonar: the emulator

```sh
python tools/sonar_emulator.py recording.son --compress &    # pretends to be a P900/M900 on port 1149
opensonar live 127.0.0.1                                       # or, in the GUI, connect to 127.0.0.1

python tools/aris_emulator.py sample.aris &                    # a .aris sonar on TCP 56888 (one client)
# in the GUI: IP 127.0.0.1, type ".aris sonar", Connect
```

### Command-line reference

```
opensonar gui
opensonar export FILE.son|FILE.aris|FILE.ddf DIR/ [--format png|tiff|npy|npz|mp4 ...] [--type xy|rtheta] [--ping N]
opensonar live IP|FILE.son [--head N] [--range [START] STOP] [--gain G] [--tvg T]
                         [--npz DIR] [--record FILE.son] [--pings N] [--no-window]
opensonar discover [--timeout S]
opensonar server [IP|FILE.son] [--head N ...] [--image] [--record FILE.son] [--bind ADDR]
opensonar ctl status|heads|discover|connect|range|gain|tvg|start|stop|record|stop_record|mark|seek|step|quit [ARGS]
```

---

## Python usage

The same API works for a file, a real sonar and the emulator ([`docs/API.md`](docs/API.md)):

```python
import numpy as np
import opensonar
from opensonar.writer import SonWriter

with opensonar.open("192.168.1.45") as sonar:            # or "recording.son", or None (discovery)
                                                      # .aris/.ddf files, or ("ip", family="aris")
    print(sonar)                                      # model, serial, heads
    head = sonar.head(0)
    head.adjust_range(1.0, 20.0)                      # live only
    with SonWriter.for_sonar("session.son", sonar) as w:
        for ping in head.pings(n=50):
            w.add(ping)
            img, geo = head.image(ping, "xy")         # np.uint16; geo["res"] = metres per pixel
            vis = np.log1p(img.astype(float))         # for display
```

You can also read the raw signal (`ping.signals`: 12-bit samples per transducer), convert older
recordings (`opensonar.writer.copy_son`) and receive pings in a thread, always the latest one
(`head.stream()`). `head.frame(ping)` gives any ping as the common `Frame` (`intensity`, `ranges`,
`beam_angles`, `vertical_fov`), the same for every family; on `.aris`/`.ddf` heads the pings already are
frames.

### Reading the image
- **XY (fan):** each pixel is `geo["res"]` metres wide. Row 0 is the farthest from the sonar, and the
  sonar sits at pixel (`origin_row`, `origin_col`).
- **R-Theta:** row 0 is the maximum range, and the columns go from the minimum to the maximum bearing.
  In the NPZ, `range_m` gives the range of each row and `angle_deg` the bearing of each column.
- The NPZ also carries `timestamp` (Unix s, UTC), `tz_offset_s`, `range_start` and `range_stop`.

---

## How it works

**Image (P900/M900).** These sonars use frequency steering: each transducer emits a chirp, and each frequency
leaves at a different angle. OpenSonar computes a spectrogram of each transducer's signal (frequency ↔
angle, time ↔ range). Then it corrects the delay of each frequency inside the chirp, applies the per-beam
gain, maps each pixel to the right sector and blends the overlapping sectors. ([`docs/IMAGING.md`](docs/IMAGING.md)).

**Image (`.aris`, `.ddf`).** The lens forms the beams: each frame is already intensities per beam and
range sample. Only the geometry is needed: the range of each sample from the timing in the frame
header, and a measured angle per beam (the lens spaces them unevenly) ([`docs/ARIS_FORMAT.md`](docs/ARIS_FORMAT.md),
[`docs/DDF_FORMAT.md`](docs/DDF_FORMAT.md)).

**File.** A `.son` is a chain of 4096-byte blocks, with configuration and pings in big-endian binary
records and the signal compressed with zlib ([`docs/SON_FORMAT.md`](docs/SON_FORMAT.md)).

**Network.** TCP on port 1149, with binary messages carrying XML and attachments. Discovery is by
multicast (224.192.45.34), and the signal comes in a 5/7-bit delta codec
([`docs/PROTOCOL.md`](docs/PROTOCOL.md)). `.aris` sonars: availability beacons on UDP 56124,
protobuf commands on TCP 56888 and frames in parts over UDP ([`docs/ARIS_LIVE.md`](docs/ARIS_LIVE.md)).

Each statement in the documents carries a confidence level (A/B/C) and its
evidence.

---

## Project status

| Part | Status |
|---|---|
| `.son` reading and XY/R-Theta images | ✅ validated against reference images (revisions 7–14) |
| PNG/TIFF/NPZ export | ✅ |
| Network protocol documented | ✅ checked with the real sonar and the emulator |
| Sonar emulator | ✅ |
| Native network driver + single API | ✅ validated against the emulator (identical images) |
| Writing `.son` | ✅ the files read back with the same images |
| Live viewer | ✅ |
| Native driver with the real sonar | ✅ connection, settings, recording; 🔄 images with real echoes ([`docs/LIVE.md`](docs/LIVE.md)) |
| Graphical interface | ✅ connection, heads, acquisition, display, recording, marks, playback, tools, export; tested offscreen with the emulator and with the real sonar |
| ZeroMQ service + Python client | ✅ tested with the emulator and the real sonar ([`docs/SERVER.md`](docs/SERVER.md)) |
| Common `Frame` layer | ✅ every family; XY fan from any frame |
| `.aris` reader/writer | ✅ against the published sample recording |
| `.aris` live driver | ✅ against the emulator (keepalive, reconnection, busy sonar); 🔄 real sonar |
| `.ddf` reader | ✅ version 3 against public recordings; 🔄 version 4, 48 beams, long-range lens |
| ROS 2 node | ✅ all families, with the emulators; optional simulator imaging-sonar output |

Known limitations: `.son` revisions 8, 9, 11 and 12 have no samples to test; the I/Q signal was only
tested with the emulator; reception of pings over UDP multicast is not
implemented, because the driver uses the TCP path. `.aris`/`.ddf`: the left/right orientation of the
beams follows the specifications and has not been checked on a scene with a known layout; `.ddf`
files do not record the sound speed (1457 m/s unless `sound_speed` is given).

---

## Tests

```sh
pip install pytest pytest-qt
python -m pytest -q tests
```

The basic tests run without data (the `.aris` and `.ddf` tests build synthetic files themselves). The ones comparing with reference images, and the network, writer
and viewer tests, use recordings in `samples/`, which are not in the repository, and are skipped without
them. The network, service and GUI tests start the emulator themselves: no sonar is needed (the GUI tests run
offscreen).
If pytest breaks because of plugins from other packages (ROS, for example), use
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests`.

---

## Layout

```
src/opensonar/
  son.py           .son reader
  image.py         R-Theta and XY images (P900/M900)
  frame.py         common Frame, XY fan of any frame
  formats/aris.py  .aris reader/writer, beam angle tables, live sample reordering
  formats/ddf.py   .ddf reader, lens model
  net.py           P900/M900 network protocol, 5/7 codec, driver and discovery
  aris_net.py      .aris live driver (beacons, protobuf commands, frame parts, reconnection)
  netsetup.py      the opensonar-net network profile
  sdk.py           single API: open → Sonar → Head → Ping / Frame, Stream
  writer.py        .son writing (SonWriter, copy_son), raw writer per family
  viewer.py        live fan window
  process.py       acquisition/recording in a separate process
  server.py        ZeroMQ service (commands + ping publishing + recording)
  client.py        client of the service (Client, Subscriber)
  media.py         NPY/NPZ/PNG/TIFF per ping with index.csv, MP4 video, multi-format export
  display.py       colour mapping (colormaps, thresholds, gamma, auto intensity)
  gui.py           graphical interface (PySide6 + pyqtgraph), client of the service
  cli.py           opensonar command (export, live, discover)
tests/             tests (without data, against reference images and against the emulator)
tools/
  sonar_emulator.py emulated P900/M900 fed by a .son
  aris_emulator.py emulated .aris sonar fed by a .aris
  capture_net.sh   traffic capture with the sonar (pcapng)
examples/          minimal subscriber of the service
packaging/         .deb and AppImage build (build.sh), container tests, icon, systemd unit, polkit rule
ros2/              ROS 2 packages (messages + node)
docs/              install, GUI guide, time sync, formats (.son/.aris/.ddf), imaging, display, protocols, service, API, live checklist, status
```

---

## License

[MIT](LICENSE).

## Disclaimer

Independent academic project, not affiliated with any manufacturer. The model names belong to their
owners and appear here only to identify the compatible equipment. The code and documentation are
original, except the beam geometry tables listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). Use at your own risk: when operating a sonar, keep the
transducers submerged.
