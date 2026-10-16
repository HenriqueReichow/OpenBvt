# opensonar GUI — user guide

`opensonar gui` is a window for operating P900 and M900 sonars: connect, pick heads, set range/gain/TVG, and
watch the fan. It is a client of the opensonar service
([`SERVER.md`](SERVER.md)), which owns the sonar connection.

```sh
pip install -e ".[gui]"
opensonar gui
```

## Window layout

```
 ● Record  ⚑ Mark  📏 Ruler │ Save screen (PNG)  Save image (TIFF 16 bit)  Export pings…
┌ Controls ─────────────┬────────────────────────────────────────────────┐
│ Connection            │                                                │
│  [Discover sonars]    │   head 0 — M2250-130     head 3 — M900-130     │
│  Model | Serial | IP  │      (fan in metres,        (one panel per     │
│  [Connect to selected]│       range rings and       ticked head)       │
│  [IP....][type][Conn.]│       bearing lines)                           │
│  [Open recording…]    │                                                │
│  [Disconnect]         │                                                │
│ Heads (tick to show)  │                                                │
│ Acquisition           │                                                │
│  Head, Range, Start/  │                                                │
│  stop, Gain, TVG,     │                                                │
│  [Start] [Stop]       │                                                │
│ Display               │                                                │
│  Colormap, Auto Int., │                                                │
│  Sensitivity, Int.,   │                                                │
│  Gamma, Smoothing,    │                                                │
│  Rings and bearings   │ ⏮ ▶ ⏭ ───────●────── 101/806  Speed 1×  ping[ ] Go │ (files only)
├───────────────────────┴────────────────────────────────────────────────┤
│ Connected … · cursor x/y/range/bearing    ● REC 00:01:23 · 1234 pings · 56.7 MB│
│                         19.0 pings/s · lost 0 · UTC 13:31:01.770 · sonar clock │
└──────────────────────────────────────────────────────────────────────────┘
```

## Before you start: the network
The sonar has a fixed IP (factory default 192.168.1.45), so the PC's wired card must be in the same
subnet. Press **Configure sonar network** (top of the Connection box). It activates the
NetworkManager profile **opensonar-net** (192.168.1.10/24 + 169.254.10.10/16 on the wired card) without a password, and
checks that the sonar answers. The .deb creates the profile at install time; the AppImage or a pip
install create it, for your user, the first time you press the button ([`INSTALL.md`](INSTALL.md)).
The sonar answers about 40 s after power-on.

## The service
- When the window opens, it looks for the service on 127.0.0.1:4545. If none is running, it starts
  one (`python -m opensonar server`) and stops it when the window closes.
- If a service was already running (for example `opensonar server` started by a script or a ROS node),
  the window uses it and **leaves it running** on close. The status bar shows "(external service)".
- Several programs can watch the same sonar at once: the service publishes every ping to all of them.

## Connection
| Control | What it does |
|---|---|
| **Configure sonar network** | activates the opensonar-net profile and pings the sonar (see above) |
| **Discover sonars** | multicast discovery (2 s), plus the beacons of the `.aris` family. For each sonar found, the list shows **Model**, **Serial**, **IP** and **State**: *free*, or *busy (another program)* when the beacon says another client has it (only one at a time). A sonar that is already connected keeps its connection. |
| **Connect to selected** / double click | connects to the selected row |
| IP field + type + **Connect** | connects to `ip` or `ip:port` (an emulator on another port, for example). The type selects the protocol: **P900 / M900** or **.aris sonar** (discovery fills it in). The last IP and type are remembered. |
| Status bar (`.aris` family) | *Connected, but no frames: the sonar may have refused the settings* when no frame arrived ~5 s after connecting; the reconnection notice when the connection dropped and came back. |
| **Open recording…** | opens a `.son`, `.aris` or `.ddf` recording on this PC. **Start** plays it at the recorded pace, once. |
| **Disconnect** | stops pinging and closes the connection |

## Heads
Each entry shows the head index, model and centre frequency, for example
`3  M900-130  900 kHz`. Ticked heads get a panel each:
- 1 head fills the window;
- 2–4 heads go in two columns;
- 5–6 heads go in three columns.

With several heads ticked, the sonar pings them **in turn**, so each head gets a share of the ping
rate (about 19 pings/s in total on an M900/2250, so ~9.5 per head with two). Changing the ticks while
pinging switches the set immediately.

## Acquisition
| Control | Notes |
|---|---|
| **Head** | the head the range/gain/TVG controls apply to |
| **Range** slider | stop range, in 0.1 m steps; applied when the slider is released |
| **Start / stop** fields | exact values in metres; applied on Enter or when leaving the field; clamped to the head's limits (M2250: 0.24–10 m, M900: 0.6–100 m) |
| **Gain**, **TVG** | receiver base gain and time-varying gain; applied on Enter or when leaving the field |
| **Start** / **Stop** | start/stop pinging the ticked heads |

A shorter range gives a higher ping rate and a finer image. The last stop range of each head model is
remembered. Range, gain and TVG are disabled for a `.son` file.

## Display
The image on screen is the u16 fan of `docs/IMAGING.md`, coloured as described in `docs/DISPLAY.md`
([`DISPLAY.md`](DISPLAY.md)). These controls never change the recorded data.

| Control | Default | Effect |
|---|---|---|
| **Colormap** | Copper | our own maps, generated by formula: **Copper**, **Grayscale**, **Jet**, **Viridis**. **.cmap folder…** adds `.cmap` files you already have (they replace a built-in map with the same name). |
| **Auto Intensity** | on | per image, the darkest 0.01 % and the brightest 0.01 % of the echoes set the colour range, smoothed over a few pings so it does not flicker |
| **Sensitivity** (manual) | 20 | threshold below which everything gets the darkest colour: raise it to hide background noise |
| **Intensity** (manual) | 10000 | the higher, the lower the saturation point and the brighter the image |
| **Gamma** (manual) | 0.5 | below 1 brightens weak echoes; 1 is linear |
| **Smoothing (3×3)** | on | the 3×3 mean of the image (sent to the service) |
| **Range rings and bearings** | on | rings at round distances and rays every 15° |

Zoom and pan: mouse wheel and drag inside a panel; right-click → "View All" to reset.

## Tools (toolbar)
| Tool | How |
|---|---|
| Cursor readout | move the mouse over a fan: the status bar shows the head, **x/y** (m), **range** (m) and **bearing** (°, 0 = straight ahead, positive to the right) |
| **📏 Ruler** (R) | click two points: the line shows the distance in metres. A third click starts a new measurement, and turning the ruler off clears it. |
| **Save screen (PNG)** | the panels as they look (colours, grid, ruler) |
| **Save image (TIFF 16 bit)** | the u16 fan on screen, lossless, cropped to the range. With several heads, one file per head (`_h<N>`). |
| **Export pings…** | (files) a range of pings of one head, in **several formats at once**: MP4 (coloured with the current display settings, at the recorded ping rate), NPY, NPZ, 16-bit PNG and TIFF (folders with `index.csv`, as in recording), XY or R-Theta. It can be cancelled. |

File names start with the UTC time (`opensonar_20261014_133101Z…`), and each dialog remembers its last
folder.

## Recording
**● Record** opens a dialog: folder, name (default `opensonar_<UTC>`) and the **formats**, any combination:

| Format | Recorded by | What you get (base path `/data/session`) |
|---|---|---|
| **raw** (`.son` / `.aris`) | the service, every ping | `session.son`: raw signal, rev 14 (P900/M900); `session.aris` for the `.aris` family, the frames with their headers. Lossless, opens in opensonar; everything else can be made from it later. Not available while playing a `.ddf` |
| **MP4 video** | the window | `session.mp4`: the panels as shown (colours, grid, ruler, several heads), 10 frames/s in real time |
| **NPY** | the service, every ping | `session_npy/h<head>/ping_000000.npy …`: XY image, uint16 |
| **NPZ** | the service, every ping | `session_npz/h<head>/…`: XY image + time, range, resolution, origin |
| **PNG / TIFF 16 bit** | the service, every ping | `session_png/h<head>/…`, `session_tiff/h<head>/…` |

- Each image folder has an **`index.csv`**: one row per ping with the file, ping number, UTC time,
  ranges, resolution and the pixel of the sonar (`origin_row`, `origin_col`). Pixel (r, c) lies at
  x = (c − origin_col)·res, y = (origin_row − r)·res metres.
- The service renders **every** recorded ping for the image formats, in a separate process, so none is
  skipped even when the screen drops frames. When you stop, it finishes the images still queued.
  This costs some ping rate (about 15–40 % on a real M900/2250 with two heads). For the highest rate,
  record only `.son` and export images later.
- While recording, the status bar shows **● REC time · pings · size · video frames**. Press
  **● Record** again to stop, and the status bar shows what was written. Closing the window stops the
  recording properly.
- The last folder and the chosen formats are remembered.

## Event marks
**⚑ Mark** (or the **M** key) asks for an optional text and creates a mark with the **UTC time**:
- it is published at once on the service's `event/<serial>` topic, so another program (the ROV
  logger, a ROS node) can line it up with its own data;
- while recording, it is appended to `<recording>.events.csv`, next to the `.son`, with columns `utc`,
  `timestamp`, `recorded_pings` (pings already in the file), `seq` and `text`.

## Playback of a `.son`
Opening a file shows the playback bar under the panels:
- **▶ / ⏸**: play or pause;
- **⏮ / ⏭**: previous or next ping (pauses);
- the **time bar**: drag to a position (shows `ping / total`);
- **Speed**: 0.25× … 4× of the recorded pace, or **max** (as fast as possible);
- **ping [ ] Go**: jump to a ping number (0 = first).

The ticked heads play together, and the file plays once (it stops at the end).

## Status bar
From left to right:
- the **connection** (live or file, model, serial, source);
- the **cursor** readout;
- the **recording** indicator;
- **pings/s** over the last 2 s;
- **lost** pings: gaps in the service's `seq` counter (in red when not 0), meaning pings the window did
  not receive (the recording is not affected);
- the **UTC time** of the last ping (already corrected for the sonar clock);
- the **sonar clock** offset. A sonar whose clock was never set shows a huge offset ("not set; times
  corrected").

## Remembered between sessions
Last IP, the ticked heads of each sonar (by serial), the last stop range of each head model, colormap,
Auto Intensity, Sensitivity, Intensity, Gamma, smoothing, rings, playback speed, the folders of each
dialog, and the window size, position and layout. They are kept in the user's Qt settings
(`~/.config/opensonar/opensonar-gui.conf` on Linux).

## Troubleshooting
| Symptom | Check |
|---|---|
| Discover finds nothing | `ping 192.168.1.45`; PC IP in 192.168.1.x; Wi-Fi not on the same subnet; sonar booted (~40 s) |
| Uniform noise, no echoes | the face must be under water, with a target in front |
| Very dark image in a small tank | the walls dominate Auto Intensity: turn it off and lower Intensity / raise Sensitivity |
| "could not start the opensonar service" | another program is using ports 4545/4546; close it, or start `opensonar server` with other ports |
| Cable unplugged and plugged back | NetworkManager may take the card over: set the IP again, then Disconnect / Connect |
