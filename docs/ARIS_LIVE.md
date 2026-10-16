# Live `.aris` sonars

Driver: `opensonar.aris_net`; API: `opensonar.open(host, family="aris")`; emulator:
`tools/aris_emulator.py`. **Validated only against the emulator so far**, not against a real sonar.

## Transport
| What | How |
|---|---|
| Discovery | the sonar broadcasts an availability beacon to UDP **56124** |
| Commands | TCP **56888**; each command is a protobuf `Command` preceded by its size (u32, big-endian) |
| Frames | UDP to the port (and optionally IP) set by `SET_FRAMESTREAM_RECEIVER`; one frame in several parts. The driver asks for the fixed port **56125** (`frame_port`, 0 = any), so a firewall rule can allow it |
| Keepalive | a `PING` command every second; without it the sonar stops sending |

Protobuf is encoded and decoded by hand (`encode`/`decode`): varint, fixed32 and length-delimited
fields are enough.

## Beacon (`Availability`)
| Field | Meaning |
|---:|---|
| 1 | serial number |
| 2 | sonar type: 0 = 1800, 1 = 3000, 2 = 1200 |
| 4 | 0 = available, 1 = busy |

## Command (`Command`)
Field 1 is the type; the sub-message goes in the field of that type.

| Type | Field | Sub-message fields |
|---:|---:|---|
| 0 `SET_DATETIME` | 2 | 1: local time text, `"%Y-%b-%d %H:%M:%S"` |
| 1 `SET_FRAMESTREAM_RECEIVER` | 3 | 1: UDP port, 2: IP (optional; default = the TCP client) |
| 3 `SET_ACOUSTICS` | 5 | 1 cookie (incremented on every send), 2 frame rate (f32), 3 samples per beam, 4 sample start delay (µs), 5 cycle period (µs), 6 sample period (µs), 7 pulse width (µs), 8 ping mode, 9 transmit on, 10 high frequency, 11 150 V on, 12 receiver gain (f32) |
| 4 `SET_SALINITY` | 6 | 1: 0 fresh, 15 brackish, 35 salt |
| 6 `SET_FOCUS` | 8 | 2: focus range in m (f32) |
| 14 `PING` | 16 | empty (keepalive) |

Connection sequence: frame receiver → date/time (English month names) → acoustic settings → salinity
→ focus, then `PING` every second. The sonar sends no frame until it gets valid acoustic settings, and
it does not acknowledge commands. Starting settings per sonar type (`aris_net.DEFAULTS`) are known-valid
examples: type 0 → 1.5–7.5 m, type 1 → 1.5–5 m, type 2 → 4–24 m, in fresh water.

Valid acoustic settings (invalid ones are ignored by the sonar):

| Field | Range |
|---|---|
| samples per beam | 128–4096 |
| sample start delay | 930–60000 µs |
| sample period | 4–100 µs |
| cycle period | 1802–150000 µs, and ≥ start delay + samples × period + 360 µs |
| frame rate | 1–15 frames/s, and 10⁶/frame rate > cycle period × pings per frame |
| receiver gain | 0–24 dB |

`adjust_range(start, stop)`: start delay = 2·start/c; the sample period is kept and the sample count
changes to cover the window (the period grows only when 4096 samples are not enough); then the cycle
period and the frame rate (at most 15) follow the rules above. `adjust_gain(g)` sets the receiver gain.
TVG is not available. The sonar may still shorten the pulse width to limit transmitted energy.

## Frame parts (`FramePart`)
| Field | Meaning |
|---:|---|
| 1 | frame index (from 1) |
| 2 | size of the sample data (beams × samples) |
| 3 | frame header, only in the first part (the bytes in use; padded to 1024 here) |
| 4 | sample bytes of this part |
| 5 | offset of field 4 in the sample data |

A frame is complete when all its bytes arrived; a part of a newer frame drops the older incomplete
ones. Live samples come **unordered** (header field `reordered` = 0): each ping of the frame fires 16
channels, and channel *c* of ping *p* goes to beam `p + map[c]·pings` with
`map = 10 2 14 6 8 0 12 4 11 3 15 7 9 1 13 5` and pings per frame 3, 6, 4, 8 for ping modes 1, 3, 6, 9
(`formats.aris.reorder`). The header layout is the one in [`ARIS_FORMAT.md`](ARIS_FORMAT.md).

## One client, keepalive and reconnection
The sonar accepts one controller at a time, so the **opensonar service is its only client**: the GUI,
ROS 2 and any other program read the frames through the service.
- Two threads run for as long as the service is connected, with or without subscribers or a GUI: one
  receives and assembles frames (the last 30 are kept while nobody reads), the other sends `PING` every
  second.
- When the command connection drops, the driver reconnects every second and sends the starting
  sequence again (frame receiver, date/time, the acoustic settings in use, salinity, focus). The service
  `status` reports it in `warning` and in `link` (frames received, seconds since the last frame,
  reconnections).
- Before connecting, the driver listens to the beacon for up to 1.5 s; if it says the sonar is busy
  (another program is connected), the connection is refused with a message saying so
  (`check_busy=False` skips it).

## Network
- The sonar gets its address by DHCP or, without a DHCP server, a link-local one (169.254.x.x): the
  PC must be on the same network (for a direct cable, a link-local address on the PC works).
- The firewall must let in UDP 56124 (beacons) and 56125 (frames); the .deb adds both rules when ufw
  is active, and removes them on uninstall.
- The `opensonar-net` profile has 169.254.10.10/16 besides 192.168.1.10/24, so the same cable and
  profile serve both sonar families.
- Only one controller at a time: a beacon with "busy" means another program is connected.
- After a connection the sonar also sends its log to the PC by syslog (UDP 514); it is not used here.

## Not covered yet
Acknowledging frame parts (field 6, ack port), rotator, telephoto lens and focus control beyond the
starting value.
