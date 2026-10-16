# opensonar API (`opensonar.open`)

One interface for the three data sources: **`.son` file**, **real sonar on the network** and
**emulated sonar** (`tools/sonar_emulator.py`). The concepts (a sonar with heads, a head with a
configuration and pings, a ping that becomes an image) are the same for the three.

```python
import opensonar

with opensonar.open("192.168.1.45") as s:        # or "recording.son", or "ip:port", or None (discovery)
    print(s)                                    # <Sonar live 192.168.1.45: …, serial …, 6 head(s)>
    head = s.head(3)
    head.adjust_range(1.0, 20.0)                # live; on a file → ApiError
    for ping in head.pings(n=100):
        img, geo = head.image(ping, "xy")       # np.uint16
```

## `opensonar.open(source=None, **options) → Sonar`

| `source` | Result |
|---|---|
| a name ending in `.son` | `FileSonar` (read-only) |
| a name ending in `.aris` | `ArisFileSonar` (read-only, one head whose pings are `Frame`s) |
| any other existing file | `ApiError` (unknown format) |
| `"ip"` or `"ip:port"` | `LiveSonar` through the native driver (`opensonar.net`); default port 1149 |
| `None` | multicast discovery; opens the first sonar that answers |

Live options: `timeout` (s per request, default 10), `sync_clock` (default `True`),
`discovery_timeout` (s, with `source=None`).

## `Sonar`

| Member | File | Live |
|---|---|---|
| `live` | `False` | `True` |
| `source`, `sonar_type`, `name`, `model`, `serial`, `firmware`, `fpga` | from the sonar record | from the `<Sonar>` sent on connection |
| `heads`, `head(i)` | list of `Head`; `head(i)` checks the index | same |
| `clock_offset` | 0 | sonar clock − local clock (s), already removed from the ping times |
| `close()`, `with` | — | closes the TCP connection |

## `Head`

| Member | Description |
|---|---|
| `index`, `model`, `transducers`, `center_freq`, `sound_speed` | configuration |
| `limits` | `(minimum range, maximum range)` in m |
| `gain`, `tvg` | current values |
| `config` | the configuration in the reader format (`opensonar.son.HeadConfig`), used by the image generator |
| `n_pings` | number of pings in the file; `None` when live |
| `ping(i=None)` | file: ping `i`, or the next one in sequence; live: a new ping |
| `pings(start=None, n=None)` | iterator; stops at the end of a file, endless when live |
| `stream(real_time=False, on_ping=None, on_demand=None)` | `Stream`: fetches pings in a thread and keeps only the latest; `on_ping(p)` sees all of them; on demand (default without `on_ping`) only requests the next one after the read |
| `adjust_range(start, stop)` | live; the values are clamped to `limits`. On a file → `ApiError` |
| `adjust_gain(g)`, `adjust_tvg(t)` | live; on a file → `ApiError` |
| `image(ping, kind="xy", smooth=True, crop=False)` | `(u16 image, geometry)`; `kind` is `"xy"` (fan) or `"rtheta"`; `smooth=False` skips the 3×3 mean; `crop=True` (XY) renders only the window within the ping range |
| `frame(ping)` | the ping as a `Frame` |

On a `.aris` file the head is a `FrameHead`: its pings already are `Frame`s, `config`, `gain`, `tvg` and
`center_freq` are `None`, `transducers` is empty, and `image()` builds the fan from the frame
(`smooth` and `crop` do not apply).

## `Frame` (common to every sonar family)
`number`, `timestamp` (Unix s, UTC), `intensity` (samples × beams; row 0 = nearest range),
`ranges` (m, increasing), `beam_angles` (rad, increasing; 0 = ahead, + = starboard), `sound_speed` (m/s),
`vertical_fov` (rad, or `None` when unknown), `n_samples`, `n_beams`.
`opensonar.frame.to_xy(frame, resolution=None)` → `(XY image, geometry)` for any frame.

## `Ping` (the same object for the three sources)
`number`, `timestamp` (Unix s, UTC), `tz_offset` (s), `range_start`, `range_stop` (m),
`signals` (one `np.uint16` per transducer, I channel; 12 bits with zero at 0x800), `signals_q` (Q channel,
empty when there is none), `max_signal()`.

## `Stream`
```python
with head.stream() as f:                # live: requests pings; file: reads in sequence
    while window_is_open():
        p = f.latest(timeout=5)         # the most recent one not handed out yet (None at end/timeout)
        show(head.image(p)[0])
```
The consumer never falls behind: intermediate pings are dropped (`f.received` counts them all). On a
file, `real_time=True` follows the recorded interval between pings. Errors of the thread are re-raised in
`latest()`.

## Recording (`opensonar.writer`)
```python
from opensonar.writer import SonWriter, copy_son

with opensonar.open("192.168.1.45") as s, SonWriter.for_sonar("session.son", s) as w:
    for p in s.head(0).pings(n=500):
        w.add(p, 0)                    # goes to disk right away; indices written on close()

copy_son("old_rev7.son", "new.son")    # converts any readable revision to rev 14
```
The file is rev 14 (`SON_FORMAT.md` §9). Without `close()` the file has no index.

## Viewer (`opensonar.viewer`)
```python
from opensonar.viewer import Viewer
with opensonar.open("192.168.1.45") as s:
    Viewer(s, head=0).run()                    # window until closed / q / end of file
```
- Draws `Head.image(ping, "xy")` with axes in metres, range rings and bearing lines.
  Keys: space pause, ↑/↓ range, `c` colours, `l` log/linear, `g` grid, `s` u16 PNG, `q` quit.
- Fetches the pings with `Head.stream()`. Without recording, the stream is **on demand**: it only
  requests a new ping after the screen read the previous one, and the screen keeps the sonar's pace
  (with the emulator: P900 14 fps, MB2250 9, M900 1.7). With `on_ping`, the thread requests without
  pause and competes with the drawing for the interpreter; to record while watching, use the separate
  process below.
- `Viewer(..., show=False)` draws without a screen (tests, figures).

### Acquisition in a separate process (`opensonar.process`)
```python
from opensonar.process import RemoteSonar
s = RemoteSonar("192.168.1.45", head=0, range_=(1, 20), record="session.son", npz=None)
Viewer(s, 0).run()
s.close()                                      # waits for the child to close the .son
```
The child process connects, applies the settings, records **every** ping and sends only the latest one
to the window, when it asks. `RemoteSonar` has the same `Sonar`/`Head`/`Stream` interface the viewer
uses, and ↑/↓ become commands to the child. It is what `opensonar live` uses with a window. With the
emulator in another process, the P900 records 18.7 pings/s and shows 9.5 fps.

## Errors
All derive from `opensonar.OpenSonarError`: `SonError` (file), `opensonar.net.NetError` (network) and
`ApiError` (API usage, such as a missing head, an adjustment on a file or a ping out of range).

## Command line on top of the API
`opensonar live SOURCE [--head N] [--range [START] STOP] [--gain G] [--tvg T] [--npz DIR] [--record FILE.son] [--pings N]`
takes an IP or a `.son`: the same command shows the sonar live or plays a recording back.
`opensonar discover` lists the sonars on the network.

## Tests
`tests/test_sdk.py` runs the same contract over a `.son` and over the emulator fed by the same file:
metadata, identical images, random access, end of file, settings, streams and `open(None)` through
discovery. `tests/test_net.py` covers the driver in detail.
