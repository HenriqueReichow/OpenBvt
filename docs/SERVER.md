# opensonar service protocol (`opensonar server`)

One process owns the sonar connection, records `.son` files and publishes every ping over
[ZeroMQ](https://zeromq.org). Any number of clients (GUI, ROS 2 node, C++, Unity, scripts) subscribe to
the pings and send commands. The wire format below needs only a ZeroMQ binding and a JSON parser: it
does not depend on Python.

```
                       ┌──────────── opensonar server ─────────────┐
sonar ◀──TCP 1149──▶   │ driver ─▶ acquisition thread ─▶ .son    │
                       │                     │                   │
                       │                     ▼                   │
clients ──REQ──▶       │ REP  :4545  (JSON commands)             │
clients ◀─SUB──        │ PUB  :4546  (ping/<serial>/<head>)      │
                       └─────────────────────────────────────────┘
```

Protocol version: **1**. It appears in every ping (`"v"`) and in `status` (`"version"`).

## 1. Running

```sh
pip install -e ".[server]"                                   # pyzmq
opensonar server                                               # waits for `connect`
opensonar server 192.168.1.45 --head 0 --head 3 --record s.son # connects, records and pings right away
opensonar server 127.0.0.1:1149 --image                        # emulator; also publishes the XY image
opensonar server --bind 0.0.0.0                                # reachable from other machines (ROV network)
```

Defaults: command port **4545** (REP) and ping port **4546** (PUB), on 127.0.0.1. The service has no
authentication, so only bind to a network interface you trust.

## 2. Commands (REQ → REP, port 4545)

Each request is one ZeroMQ frame with a UTF-8 JSON object: `{"cmd": "<name>", ...arguments}`. The
reply is one frame with a JSON object: `{"ok": true, ...result}` or `{"ok": false, "error": "<text>"}`.
A REQ socket must receive the reply before sending the next request.

| `cmd` | Arguments | Result |
|---|---|---|
| `status` | — | `version`, `connected`, `source`, `serial`, `running`, `heads` (being pinged), `image`, `smooth`, `speed`, `clock_offset` (sonar − local clock, s), `published`, `recording` (path or null), `recorded`, `record` (null, or `{path, pings, marks, elapsed (s), bytes}`), `playback` (files: `{position, n_pings}`, position = next ping), `error` (last acquisition error or null), `rep_port`, `pub_port` |
| `discover` | `timeout` (s, default 2), `probe` (default true) | `sonars`: list of the discovery answers (`Host`, `Port`, `UUID`, `MulticastIP`, …); with `probe`, the service connects briefly to each one and adds `Model`, `Serial` and `Heads` (or `ProbeError`) |
| `connect` | `source`: `"ip"`, `"ip:port"`, path of a `.son` (path on the **server**), or null (first sonar found by discovery); optional `timeout`, `sync_clock` | the same as `heads` |
| `disconnect` | — | — (also stops acquisition and closes any recording) |
| `config` | `head` (optional) | `heads`: the full configuration of each head, enough to rebuild the images from the raw signal (`opensonar.son.config_from_dict`): `model`, `center_freq`, `sound_speed`, `range_min/max`, `sample_rate`, `pulse_sample_rate`, `element_spacing`, `mapping_method`, `transducers` (index, high/low frequency, flags, position, rotation, oriented), `pulse` (chirp replica, base64 complex64 LE), `normalization` (base64 float32 LE), `beams`, `beam_angles` (rad, positive to the right), `config_id` |
| `heads` | — | `source`, `live`, `model`, `serial`, `firmware`, `sonar_type`, `heads`: list of `{index, model, limits: [min, max] m, transducers, gain, tvg, center_freq, n_pings}` (`n_pings` is null when live) |
| `range` | `head`, `start` (m, null = minimum), `stop` (m) | — (values clamped to the head limits) |
| `gain` | `head`, `value` | — |
| `tvg` | `head`, `value` | — |
| `start` | `heads`: list of indices (default `[0]`), `image`: false, true (= `["xy"]`) or a list of `"xy"`/`"rtheta"`, `smooth`: bool (default true, 3×3 mean on the image), `speed`: playback speed of a file (null = as fast as possible; 1 = recorded pace) | `heads` |
| `seek` | `index` | `position` — moves every head of a **file** to ping `index` (keeps playing if it was) |
| `step` | `delta` (1 = next, −1 = previous), optional `image`, `smooth` | `position`, `number` — stops playback of a file and publishes that one ping |
| `mark` | `text` | `mark` (`utc`, `timestamp`, `text`, `seq`, `recorded_pings`), `file` — event mark: published on `event/<serial>` and, while recording, appended to `<recording>.events.csv` |
| `image` | `enabled`, `smooth` | — (changes the published image while running) |
| `stop` | — | — |
| `record` | `path` (on the server; `.son` optional), `formats`: any of `son` (default), `npy`, `npz`, `png`, `tiff` | `path`, `formats` — `son` writes `<path>.son`; each image format writes every ping's XY image (rendered in a child process, none skipped) to `<path>_<fmt>/h<head>/ping_NNNNNN.<ext>` plus `index.csv` (file, number, utc, timestamp, tz_offset, range_start, range_stop, kind, rows, cols, res, origin_row, origin_col) |
| `stop_record` | — | `path`, `pings` (number recorded), `bytes`, `marks`, `events` (CSV path or null), `formats`, `images` (pings written as images), `error` — waits until the queued images are written |
| `quit` | — | — (the service exits) |

- `range`, `gain` and `tvg` only work live. On a file they return an error.
- `start` pings the listed heads in turn (round robin), one ping per request. With a file, it plays the pings in sequence once and then stops (`running` becomes false).
- **Recording lives in the service.** Each ping is written to the `.son` in the acquisition thread
  before it is published. Recording does not depend on any client being connected, and a slow or
  closed client never causes a gap. The file is rev 14
  (`docs/SON_FORMAT.md` §10). `record` can start before or after `start`. `stop_record`,
  `disconnect` and `quit` close the file (index included).

Example (any language):
```
→ {"cmd": "connect", "source": "192.168.1.45"}
← {"ok": true, "source": "192.168.1.45", "live": true, "model": "M900/2250-130", "serial": "2920060", "heads": [...]}
→ {"cmd": "range", "head": 3, "start": 1, "stop": 20}
← {"ok": true}
→ {"cmd": "start", "heads": [3], "image": false}
← {"ok": true, "heads": [3]}
```

## 3. Pings (PUB → SUB, port 4546)

One **multipart** ZeroMQ message per ping:

| Part | Content |
|---|---|
| 0 | topic, UTF-8: `ping/<serial>/<head>` (for example `ping/2920060/3`) |
| 1 | metadata, UTF-8 JSON (below) |
| 2 … 1+n_i | I channel of each transducer, in order: `uint16` little-endian, `signals[k]` samples each |
| … n_q parts | Q channel of each transducer (only for I/Q sonars; `signals_q` is empty otherwise) |
| then | one part per entry of `images` (XY and/or R-Theta): `uint16` little-endian, row-major, `rows × cols` |

Metadata:

| Field | Meaning |
|---|---|
| `v` | protocol version (1) |
| `serial`, `head` | sonar serial (string) and head index, the same as in the topic |
| `seq` | counter of the pings published since the service started, across all heads (never reset): consecutive values mean nothing was lost between the service and this subscriber |
| `number` | ping number given by the sonar (or by the file) |
| `timestamp` | ping time, **Unix seconds UTC**, already corrected for the sonar clock (the driver measures sonar − local clock offset on connection) |
| `utc` | the same instant, ISO 8601 with `Z` (for example `2026-10-14T13:31:01.770000Z`) |
| `tz_offset` | local time zone offset in seconds, as recorded in `.son` files |
| `range_start`, `range_stop` | metres |
| `signals`, `signals_q` | number of samples of each I / Q part |
| `dtype` | always `"<u2"` |
| `image` | null, or the XY entry of `images` (older clients) |
| `images` | list of `{kind: "xy", rows, cols, res, origin_row, origin_col}` and/or `{kind: "rtheta", rows, cols, res, range_first, range_last, angle_first, angle_last}` (R-Theta: row 0 = farthest, columns from the leftmost beam; angles in rad, positive to the right) |
| `bins`, `beams`, `res` | rows and columns of the R-Theta image for this ping and its range resolution (m); they change with the range |
| `bin_near`, `bin_far` | m, ranges of the nearest and farthest R-Theta rows |
| `gain`, `tvg`, `sound_speed` | the head's settings when the ping was sent |
| `config_id` | id of the head configuration (`config` command); changes only if the configuration does |

Signals are the raw 12-bit samples with zero at 0x800, the same as `Ping.signals` in the Python API
(`docs/API.md`). The XY image is the u16 fan of `docs/IMAGING.md`,
cropped to the window within `range_stop` (uncropped, it covers the head's maximum range: 5144×9500
pixels for an M900-130 at 10 m, 1.5 s per ping, against 506×1035 and 0.05 s cropped). The pixels are
the same as in the full image. The
sonar sits at pixel (`origin_row`, `origin_col`) and row 0 is the farthest. Pixel (r, c) lies at
x = (c − origin_col)·res and y = (origin_row − r)·res metres.

### Event marks (`event/<serial>`)
One message with two parts: the topic and a JSON object `{v, serial, utc, timestamp, text, seq,
recorded_pings, source}`. `seq` is the `seq` the next ping will carry, so a mark can be placed between
pings. While recording, every mark is also written to `<recording>.events.csv` (columns `utc`,
`timestamp`, `recorded_pings`, `seq`, `text`). The `.son` format reserves records for event marks, but
their layout is not mapped yet (`SON_FORMAT.md`), so the marks live next to the file. Python:
`opensonar.client.EventSubscriber`.

### Choosing what to receive
ZeroMQ filters by **topic prefix**:
- `ping/` gives every ping (`event/` gives the marks);
- `ping/<serial>/` gives one sonar;
- `ping/<serial>/<head>` gives one head. Since `ping/S/1` is also a prefix of `ping/S/10`, compare the
  whole topic (part 0) after receiving.

### Delivery
- PUB drops messages for a subscriber that falls more than ~1000 messages behind (ZeroMQ high-water
  mark). Use `seq` to detect it. The `.son` recording is not affected.
- **Every ping is published**, in order. The XY image is rendered in a separate thread, only for the
  newest queued ping of each head. If rendering cannot keep up (for example two long-range heads at full rate), the
  older pings go out without an image (`image` is null). The ping rate and the recording do not slow
  down.
- A subscriber only receives pings published after it connected (ZeroMQ "slow joiner"). Connect first,
  then send `start`.

## 4. Clients

**Python** (`opensonar.client`):
```python
from opensonar.client import Client, Subscriber
c = Client("tcp://127.0.0.1:4545")
c.connect("192.168.1.45")
c.range(3, 1, 20)
with Subscriber("tcp://127.0.0.1:4546", head=3) as sub:
    c.start([3])
    c.record("/data/session.son")
    for msg in sub:                     # msg.meta, msg.ping (opensonar Ping), msg.image (np.uint16 or None)
        print(msg.meta["utc"], msg.ping.number)
```
Minimal example: `examples/subscriber.py`. Command line: `opensonar ctl <command>` (for example
`opensonar ctl range 3 1 20`, `opensonar ctl start 0 3 image`, `opensonar ctl record s.son`,
`opensonar ctl status`).

**C++** (cppzmq + nlohmann/json), in outline:
```cpp
zmq::socket_t sub(ctx, zmq::socket_type::sub);
sub.connect("tcp://127.0.0.1:4546");
sub.set(zmq::sockopt::subscribe, "ping/");
std::vector<zmq::message_t> parts;
zmq::recv_multipart(sub, std::back_inserter(parts));
auto meta = nlohmann::json::parse(parts[1].to_string());
const uint16_t* tr0 = parts[2].data<uint16_t>();       // little-endian hosts: use as is
```

**ROS 2**: a node subscribes as above and republishes with `header.stamp` taken from `timestamp`
(UTC, already corrected), so it lines up with the ROV's other topics. This node is a later stage.

**Unity / C#**: NetMQ (`SubscriberSocket`, `ReceiveMultipartFrames`) plus any JSON parser. The same
layout applies.

## 5. Tests
`tests/test_server.py` runs against the emulator:
- message round trip;
- errors returned as replies;
- range/gain/TVG;
- **two subscribers at the same time** (all of them see every `seq`);
- **gap-free recording** (the `.son` has every published ping, in order, with the same signals and
  images);
- recording with nobody subscribed;
- `opensonar ctl`;
- playing a `.son` file to the end.

With the real sonar (2026-10-14, heads 0 and 3 in turn): about 9.5 pings/s per head, UTC times
correct, and 52 + 52 pings recorded without gaps.
