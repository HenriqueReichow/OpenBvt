# `.aris` recordings

Read by `opensonar.formats.aris` and opened with `opensonar.open("file.aris")`.

## Layout
| Bytes | Content |
|---:|---|
| 1024 | file header; only the signature at offset 0 is used (`0x05464444`) |
| 1024 | frame header of frame 0 |
| samples × beams | frame 0 intensities, u8 (0–255 ↔ 0–80 dB), row-major: one row of every beam per sample |
| … | the same for every frame; all frames have the same size |

Number of frames = (file size − 1024) / (1024 + samples × beams); the count in the file header is ignored.

## Frame header fields used (little-endian)
| Offset | Type | Field |
|---:|---|---|
| 0 | u32 | frame index |
| 4 | u64 | sonar time, µs since 1970 (UTC) |
| 12 | u32 | signature `0x05464444` |
| 20 | u64 | recording PC time, µs since 1970 (used when the sonar time is 0) |
| 436 | u32 | ping mode: 1–2 → 48 beams, 3–5 → 96, 6–8 → 64, 9–12 → 128 |
| 452 | u32 | sample period (µs) |
| 464 | f32 | sound speed (m/s) |
| 468 | u32 | samples per beam |
| 476 | u32 | sample start delay (µs) |
| 480 | u32 | 1 = telephoto lens |
| 484 | u32 | sonar type: 0 = 1800, 1 = 3000, 2 = 1200 |
| 488 | u32 | serial number |
| 516 | u32 | 1 = samples already ordered (always so in recordings) |

## Geometry
- range of sample *i* = (start delay + *i* × sample period) × 10⁻⁶ × sound speed / 2;
- beam 0 is the right-most (starboard) one. The lens spaces beams unevenly, so each beam angle comes
  from a measured table chosen by beam count and lens (`BEAM_ANGLES`: standard 48/96, 64/128, telephoto
  48/96);
- the `Frame` has the columns in increasing angle, so beam 0 is the last column.

## Not covered yet
Live data (samples arrive unordered), the vertical aperture of the beams (`vertical_fov` is `None`).
