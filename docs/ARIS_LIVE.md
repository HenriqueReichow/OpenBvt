# Live `.aris` sonars

Driver: `opensonar.aris_net`; API: `opensonar.open(host, family="aris")`; emulator:
`tools/aris_emulator.py`. **Validated only against the emulator so far**, not against a real sonar.

## Transport
| What | How |
|---|---|
| Discovery | the sonar broadcasts an availability beacon to UDP **56124** |
| Commands | TCP **56888**; each command is a protobuf `Command` preceded by its size (u32, big-endian) |
| Frames | UDP to the port (and optionally IP) set by `SET_FRAMESTREAM_RECEIVER`; one frame in several parts |
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

Connection sequence: date/time → frame receiver → acoustic settings → salinity → focus, then `PING`
every second. Starting settings per sonar type are in `aris_net.DEFAULTS`.

`adjust_range(start, stop)`: start delay = 2·start/c, sample period = ⌈2·(stop − start)/(c·samples)⌉
clamped to 4–100 µs, cycle period = delay + period·samples + 360 µs. `adjust_gain(g)` sets the receiver
gain. TVG is not available.

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

## Not covered yet
Acknowledging frame parts (field 6, ack port), rotator, telephoto lens and focus control beyond the
starting value.
