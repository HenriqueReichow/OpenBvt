# OpenSonar

**Open software for imaging sonars on Linux, for the P900 and M900 for now: a graphical interface to operate the
sonar and record its data, a service that shares the pings with other programs (ROV, ROS 2, C++,
Unity), and a Python SDK for `.son` files and images. No Windows and no closed
library.**

## What it is

The P900 and M900 are multibeam imaging sonars used on ROVs and AUVs: each ping
becomes an acoustic "photo" of what lies ahead, a fan of tens of metres, even in murky water. They come
with closed software: a viewer for Windows only, and a binary SDK. That makes it hard to put the sonar
on a Linux vehicle computer, record it alongside the other sensors, or process its data in your own
code.

OpenSonar is an open alternative, written from scratch, with its own specification of the file format,
the image algorithm and the network protocol (`docs/`). The goal is a generic tool for the main imaging
sonars, aimed at 3D reconstruction; the P900 and M900 are the first ones supported. It was developed as an undergraduate thesis (TCC) and is MIT-licensed.

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
                 │  Python SDK         │   driver (TCP 1149), .son reader/writer, XY/R-Theta images
                 └──────────┬──────────┘
                      sonar or .son file
```

## What it does

| | |
|---|---|
| **Operate the sonar** | discover sonars on the network, connect, pick heads (several side by side), set range, gain and TVG, start/stop; one click sets up the PC's network |
| **See** | the fan in metres with range rings and bearings (Copper, gamma, Auto Intensity, Sensitivity/Intensity); cursor range/bearing, distance ruler, zoom |
| **Record** | any combination of `.son` (raw), MP4 video, NPY, NPZ, 16-bit PNG/TIFF per ping with an index of UTC times; event marks with UTC time and text for syncing with the ROV |
| **Play back** | `.son` files with a time bar, speed, step and go-to-ping; export a range of pings in several formats |
| **Share** | the service publishes every ping (raw signal + optional image, corrected UTC time) to any number of programs over ZeroMQ, and takes commands ([`docs/SERVER.md`](docs/SERVER.md)) |
| **Program** | a Python API for files and live sonars: `opensonar.open(...)`, XY/R-Theta images, `.son` writing ([`docs/API.md`](docs/API.md)) |
| **ROS 2** | `ros2/`: a Humble node publishing the fan, the raw pings (with what is needed to reprocess them), head info and diagnostics ([`ros2/README.md`](ros2/README.md)) |
| **Install** | `.deb` for Ubuntu 22.04/24.04 and an AppImage, with Python bundled: menu entry, `opensonar` command, optional systemd service ([`docs/INSTALL.md`](docs/INSTALL.md)) |
| **Test without hardware** | an emulated sonar that speaks the protocol and replays a `.son` |

**Quick start:** install the package, open **opensonar** from the menu, press **Configure sonar network**,
**Discover sonars**, **Connect to selected** and **Start**. Details below.

The images were validated against reference images of the same recordings: correlation above 0.999.

---

## Supported sonars

For now, the **P900** and **M900** families. More sonar types are planned.

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

---

## Installation

### Packages (no Python setup needed)
```sh
sudo apt install ./opensonar_4.1.0_amd64.deb      # Ubuntu 22.04 / 24.04: menu entry, `opensonar` command,
                                                 # sonar network profile, optional systemd service
./opensonar-4.1.0-x86_64.AppImage                  # or: any Linux, no installation
```
Python and every dependency are bundled. The .deb also creates the **opensonar-net** network profile
(192.168.1.10/24 on the wired card), so the GUI's **Configure sonar network** button sets up the
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
`opensonar network`), and that is all. Without them, by hand: the sonar has a fixed IP. The factory default is **192.168.1.45**, so the PC's wired card must be in the
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
- **Discover sonars**: lists the sonars on the network with model, serial and IP. Select one and press
  **Connect to selected** (or double-click it).
- Or type the IP (`192.168.1.45`, or `ip:port`) and press **Connect**. The last IP is remembered.
- Or **Open .son…** to play a recording back, at the pace it was recorded.

### 4. Choose heads
The **Heads** list shows each head with its model and frequency (a dual-frequency M900/2250 has six:
M2250-130/90/45 and M900-130/90/45). Tick the ones to show: each gets its own panel, side by side.
The selection can change while pinging.

### 5. Acquire
In **Acquisition**, pick the head to control, then set the **range** (slider, or start/stop in metres),
**Gain** and **TVG**, and press **Start** / **Stop**. With several heads ticked, the sonar pings them in
turn. Range, gain and TVG only apply to a live sonar, not to a file.

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
- **● Record**: choose a folder, a name and **any combination of formats**: `.son`, MP4 video (as on
  screen), NPY, NPZ, PNG and TIFF 16 bit (one file per ping + `index.csv` with UTC time and geometry).
  The service records every ping, and the status bar shows time, pings and size. **⚑ Mark** (key M) adds an event mark with UTC time and text, saved next to the
  recording (`.events.csv`) and published for other programs.
- Mouse over the fan: x/y, range and bearing in the status bar. **📏 Ruler** (key R): distance between
  two clicks. **Save screen (PNG)** / **Save image (TIFF 16 bit)**.
- A `.son` opened in the window gets a playback bar: play/pause, previous/next ping, time bar, speed,
  go to ping; **Export pings…** saves a range in several formats at once (MP4, NPY, NPZ, PNG, TIFF).
- The status bar also shows pings/s, lost pings, the UTC time of the last ping and the sonar clock
  offset. Settings (IP, heads, display, window) are remembered.

Tips: the transducer face must be under water, otherwise the image is uniform noise. In a small
container the walls dominate Auto Intensity, so turn it off and lower the Intensity. More in
[`docs/GUI.md`](docs/GUI.md).

---

## Using the command line

### `.son` recordings

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
Protocol (JSON commands + multipart pings, for any language): [`docs/SERVER.md`](docs/SERVER.md).

---

### Without a sonar: the emulator

```sh
python tools/sonar_emulator.py recording.son --compress &    # pretends to be a sonar on port 1149
opensonar live 127.0.0.1                                       # or, in the GUI, connect to 127.0.0.1
```

### Command-line reference

```
opensonar gui
opensonar export FILE.son DIR/ [--format png|tiff|npy|npz|mp4 ...] [--type xy|rtheta] [--ping N]
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
(`head.stream()`).

### Reading the image
- **XY (fan):** each pixel is `geo["res"]` metres wide. Row 0 is the farthest from the sonar, and the
  sonar sits at pixel (`origin_row`, `origin_col`).
- **R-Theta:** row 0 is the maximum range, and the columns go from the minimum to the maximum bearing.
  In the NPZ, `range_m` gives the range of each row and `angle_deg` the bearing of each column.
- The NPZ also carries `timestamp` (Unix s, UTC), `tz_offset_s`, `range_start` and `range_stop`.

---

## How it works

**Image.** P900 and M900 sonars use frequency steering: each transducer emits a chirp, and each frequency
leaves at a different angle. OpenSonar computes a spectrogram of each transducer's signal (frequency ↔
angle, time ↔ range). Then it corrects the delay of each frequency inside the chirp, applies the per-beam
gain, maps each pixel to the right sector and blends the overlapping sectors. ([`docs/IMAGING.md`](docs/IMAGING.md)).

**File.** A `.son` is a chain of 4096-byte blocks, with configuration and pings in big-endian binary
records and the signal compressed with zlib ([`docs/SON_FORMAT.md`](docs/SON_FORMAT.md)).

**Network.** TCP on port 1149, with binary messages carrying XML and attachments. Discovery is by
multicast (224.192.45.34), and the signal comes in a 5/7-bit delta codec
([`docs/PROTOCOL.md`](docs/PROTOCOL.md)).

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

Known limitations: `.son` revisions 8, 9, 11 and 12 have no samples to test; the I/Q signal was only
tested with the emulator; reception of pings over UDP multicast is not
implemented, because the driver uses the TCP path.

---

## Tests

```sh
pip install pytest pytest-qt
python -m pytest -q tests
```

The basic tests run without data. The ones comparing with reference images, and the network, writer
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
  image.py         R-Theta and XY images
  net.py           network protocol, 5/7 codec, driver and discovery
  sdk.py           single API: open → Sonar → Head → Ping, Stream
  writer.py        .son writing (SonWriter, copy_son)
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
  sonar_emulator.py emulated sonar fed by a .son
  capture_net.sh   traffic capture with the sonar (pcapng)
examples/          minimal subscriber of the service
packaging/         .deb and AppImage build (build.sh), container tests, icon, systemd unit, polkit rule
docs/              install, GUI guide, time sync, format, imaging, display, protocol, service, API, live checklist, status
```

---

## License

[MIT](LICENSE).

## Disclaimer

Independent academic project. The model names belong to their owners and appear here only to identify
the compatible equipment. All the code and documentation are original. Use at your own risk: when operating a sonar, keep the
transducers submerged.
