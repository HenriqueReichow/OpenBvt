# `.ddf` recordings

Read by `opensonar.formats.ddf` and opened with `opensonar.open("file.ddf")` (option `sound_speed`, m/s:
the files do not record it; default 1457). Read-only, one head whose pings are `Frame`s, like `.aris`.

**Checked:** version 3 (`DDF\x03`), high frequency, 96 beams, against public recordings. **Not checked:**
other versions (read only when the file size matches a known layout), low frequency / 48 beams, and
the long-range lens. The left/right orientation of the beams follows the reference readers; it has not
been checked against a scene with a known layout yet.

## Layout (little-endian)
| Bytes | Content |
|---:|---|
| 512 (v3) | file header |
| 256 (v3) | frame header |
| samples × beams | intensities, u8, row-major: one row of every beam per sample; beam 0 = right-most |
| … | the same for every frame |

Number of frames = (file size − file header) / (frame header + samples × beams). For version 4 the
layouts tried are (1024, 1024) and (512, 1024) bytes; sources disagree on the file header size.

## File header fields used
| Offset | Type | Field |
|---:|---|---|
| 0 | 4 bytes | `DDF` + version byte |
| 4 | i32 | frames (informative; the size is what counts) |
| 12 | i32 | 1 = high frequency / high resolution |
| 16 | i32 | beams (48 low, 96 high) |
| 20 | f32 | sample rate (Hz) |
| 24 | i32 | samples per beam (typically 512) |
| 40 | i32 | reverse flag (kept, not used yet) |
| 44 | i32 | serial number |

## Frame header fields used (version 3)
| Offset | Type | Field |
|---:|---|---|
| 0 | i32 | frame number |
| 4 | i32 | frame time, Unix s (whole seconds) |
| 12 | 4 bytes | signature |
| 20–44 | i32 × 7 | local year, month, day, hour, minute, second, hundredths |
| 52 | i32 | window start code |
| 56 | i32 | window length code |
| 68 | i32 | receiver gain |
| 192 | i32 | config flags: bit 0 = classic (1) / extended (0) windows, bit 1 = long-range lens |

Time: the local fields give the hundredths; their offset to `frame time` (rounded to 15 min) is the
time zone, so the stamp is UTC with 0.01 s resolution.

## Geometry
- delay period per window-start step: serial ≤ 18 → 1.024 ms (low) / 0.512 ms (high); otherwise
  1.144 ms / 0.572 ms;
- range of sample *n* = c/2 · (start code × delay period + (n + 0.5) / sample rate);
- beam angles: the lens spaces beams unevenly; beam number = f · (a θ³ + b θ² + c θ + d) (θ in degrees,
  0-based from the left-most beam) per beam count, inverted numerically (`beam_angles`); about 28.5°
  across, narrower beams towards the edges;
- vertical aperture 14° (`Frame.vertical_fov`).
