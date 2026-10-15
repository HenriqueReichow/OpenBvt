# `.son` format

The recording format of the P900 and M900 sonars, as opensonar reads (`son.py`) and writes
(`writer.py`) it. Described from 12 recordings (revisions 7, 10, 13 and 14) and from the files the real
sonar session produced.

Status marks: **A** = checked in 3 or more files · **B** = seen in fewer files · **C** = hypothesis.

---

## 1. General rules

| Rule | Status |
|---|---|
| Every integer, float and double of the structure is **big-endian** | A |
| The **signal samples** (after decompression) are u16 **little-endian**, unless the record's byte-swap flag is set | A |
| Strings: u32 BE length + bytes, no terminator | A |

## 2. Block layer

| Item | Status |
|---|---|
| The file is a sequence of **4096-byte** blocks; block N is at offset `N × 4096` | A |
| Each block starts with **u32 next block + u32 previous block** (doubly linked list); the payload is the remaining 4088 bytes | B |
| End of chain: next = 0 | B |
| An object (configuration, ping, index) = the concatenation of its chain's payloads | B |

## 3. Block 0 — file header

| Offset | Type | Value | Meaning | Status |
|---|---|---|---|---|
| 0x00 | 8 bytes | `89 53 4F 4E 0D 0A 1A 0A` (`\x89SON\r\n\x1a\n`) | Signature | B |
| 0x08 | u16 | 1 | Block layer version? | C |
| 0x0A | 6 bytes | `00 00 00 00 10 00` | Contains 4096 (block size?) | C |
| 0x10 | u32 | 0xFFFFFFFF | Unknown | C |
| 0x14 | u32 | 0 | Unknown | C |
| **0x18** | u16 | 7–14 | **Format revision** | A |
| 0x1A | u32 | 1 | First block of the configuration | A |
| 0x1E | u16 | 5 | Number of index entries | A |
| 0x20 | N × (u16 type, u32 block) | — | Index table | A |
| after the table | — | filler (`CD CD …`) to the end of the block | ignored | B |

### Indices
| Type | Content (chain pointed to) | Status |
|---|---|---|
| 1 | u32 number of heads; for each: u32 number of pings + block number of each ping | A |
| 2 | u32 total pings + **10-byte** entries: u32 order of the ping in the file, u16 head, u32 ping block | A |
| 3, 4, 5 | u32 count (0 in the samples) + list | B / C (navigation, orientation, event marks?) |

## 4. Records (shared by configuration and ping)

Each record is **u16 revision + u16 type + u32 total size (including these 8 bytes)**, followed by the
content. The sequence ends with a record of **type 0x1F**. The record revision follows the file
revision, and the content of each type depends on it. (A)

## 5. Configuration (chain starting at the block of field 0x1A)

| Type | Name | Content | Status |
|---|---|---|---|
| 1 | features | text `<features />` or empty | B |
| 2 | sonar | see below | A |
| 4 | HEAD_SETTINGS | one per head: model, ranges, gain, TVG, frequencies, tables | A |
| 3 | transducer | one per transducer | A |
| 0x11 | orientation | subtype 1/2: head mounting; subtype 6: position and angle of a transducer | B |
| 0x1F | end | — | A |

### Sonar (type 2), rev ≥ 10
| Order | Type | Examples | Meaning | Status |
|---|---|---|---|---|
| 1 | u16 | 0x0123 (P900-130), 0x0100 (M900-130), 0x0200 (MB2250-45); 0xFFFF if unknown | Sonar type | B |
| 2 | string | "", "M900-130" | Sonar name | B |
| 3 | 8 bytes | zeros | Unknown | C |
| 4 | string | "2058", "1721" | Serial number | A |
| 5 | 4 bytes | zeros | Unknown | C |
| 6 | u16 | 6262, 7103 | Firmware | A |
| 7 | u16 | 0, 0x1B79 | Unknown (FPGA?) | C |

### HEAD_SETTINGS rev ≥ 12, in order

| # | Type | MB2250-45 | P900-130 | Meaning | Status |
|---|---|---|---|---|---|
| 1 | u16 | 4 | 4 | Head type (0 on older heads) | B |
| 2 | u8 | 0 | 0 | Unknown | C |
| 3 | string | "MB2250-45" | "P900-130" | **Head model** | A |
| 4 | string | "0" | "0" | Unknown | C |
| 5 | f32 | 0.000356 | 0.00089 | **Element spacing** `d` (m) | A |
| 6 | f32 | 0.24816 | 0.61995 | **Start (min.) range** in m | A |
| 7 | f32 | 10.0 | 4.98004 | **Stop (max.) range** in m | A |
| 8 | f32 | 5.0 | 10.0 | **Gain** | A |
| 9 | u16 | 2500 | 250 | **TVG × 1000** | A |
| 10, 11 | u8, u8 | 0, 0 | 0, 0 | Unknown | C |
| 12 | u32 | 3 000 000 | 1 200 000 | **Signal sampling rate** (Hz) | A |
| 13 | u32 | 24 000 000 | 24 000 000 | **Reference clock** (Hz; 12 MHz on the P450) | A |
| 14 | u32 | 2 250 000 | 900 000 | **Centre frequency** (Hz) | A |
| 15 | u16 | 1445 | 1540 | **Sound speed** (m/s) | A |
| 16–20 | 5 × u16 | 0,256,256,0,0 | 0,256,256,0,0 | Unknown | C |
| 21 | u32 | 0 | 0 | Unknown | C |
| 22–25 | 4 × f32 | 0 | 0 | Unknown | C |
| 26, 27 | string, string | "" | "" | Unknown | C |
| 28 | f32 | 0 | 0 | Unknown | C |
| 29 | u32 | 3 000 000 | 600 000 | Pulse start frequency | B |
| 30 | u32 | 1 500 000 | 300 000 | Pulse stop frequency | B |
| 31 | 4 bytes (float) | 5.0 | 1.0 | Pulse time-bandwidth | B |
| 32 | u32 N | 7735 | 19323 | Size of the pulse table | A |
| 33 | N × f32, then N × f32 | — | — | Real and imaginary parts of the **chirp replica** | A |
| 34 | u32 M | 128 | 128 | Size of the gain table | A |
| 35 | M × f32 | 3.0; 2.9989; 2.9956… | same | **Per-beam gain** | A |

The record ends exactly after item 35. (A)

### Transducer (type 3, rev ≥ 11) — 17 bytes
| Type | P900-130 | MB2250-45 | Meaning | Status |
|---|---|---|---|---|
| u8 | 0…5 | 0, 1 | Transducer index | B |
| u32 | 1 200 000 | 3 000 000 | **Upper frequency** of the sector | A |
| u32 | 600 000 | 1 500 000 | **Lower frequency** of the sector | A |
| f32 | 0 | 0 | Mounting angle (rad) | B |
| u16 | 16,1,0,1,0,17 | 0,1 | Orientation flags: 0 RIGHT, 1 LEFT, 0x10 RIGHTALT, 0x11 LEFTALT | A |
| u16 | 0xFFFF | 0xFFFF | Number of elements? | C |

### Orientation (type 0x11) — 82 bytes
A u16 subtype and 12 bytes, then BE doubles from offset 14. Subtype 6: **x, y, z (m) and three
rotations (degrees)**. (B)

| Subtype | Where | Content | Status |
|---|---|---|---|
| 1, 2 | configuration, after HEAD_SETTINGS (head mounting) | subtype + zeros | B |
| 3, 4 | each ping (positioner, tilt) | subtype, 8 zeros, `0001 0002` (3) or `0001 0004` (4), zeros | B |
| 5 | after each transducer when there is no per-transducer orientation | subtype + zeros | A |
| 6 | after each transducer with orientation | subtype, 12 zeros, x, y, z, 3 rotations, 20 zeros | A |

| Head | Transducers: x, y (m), Z rotation (°) |
|---|---|
| P900-130 | (−0.005, 0.075, 21.5) (0.017, 0.075, 64.2) (0.005, 0.075, −21.5) (−0.005, 0.075, 21.5) (−0.017, 0.075, −64.2) (0.005, 0.075, −21.5) |
| MB2250-45 | (0.025, 0, −23.6) (−0.025, 0, 23.6) |

There is no "number of beams" field: each transducer gives 128 beams, the size M of the gain table.
The sector geometry is in `IMAGING.md`.

## 6. Ping (chain pointed to by index type 1)

### Ping header — 16 bytes
| Offset | Type | Example | Meaning | Status |
|---|---|---|---|---|
| 0 | u16 | 14 | Revision | A |
| 2 | u32 | 0x6759C3B5 | Time the ping was written (Unix s) | B |
| 6 | u16 | 926 | ms of that time | B |
| 8 | i32 (rev > 6) | 10800 | Time zone in s | B |
| 12 | u32 | 1 | Order of the ping in the file | B |

### Ping records
| Type | Content | Status |
|---|---|---|
| 0x10 | **Ping info**: u32 ping number; u32 Unix s + u16 ms (ping time); i32 time zone (rev > 6); f32 start + f32 stop range (rev > 7); u16 (rev > 10); u32 count + list of i32 (rev > 13) | A |
| 0x11 | Ping orientation (subtypes 3 and 4) | B |
| 0x12 | Navigation | C |
| **0x13** | **Signal of one transducer** (one record per transducer) | A |
| 0x14, 0x16, 0x17 | Unknown | C |
| 0x1F | End | A |

The sound speed is not stored per ping; it comes from HEAD_SETTINGS. (B)

### Signal record 0x13 (rev ≥ 10)
| Field | Type | Meaning | Status |
|---|---|---|---|
| byte swap | u8 | 1 = samples in big-endian | A |
| compression | u32 | 0 = raw, 1 = zlib, **2 = XOR of each byte with 0x4C, then zlib** | A |
| I channel | u32 samples, u32 bytes, data | compressed according to the mode | A |
| Q channel | u32 samples, u32 bytes, data | **empty** in every sample file | A |

- Samples: u16, 12 bits centred on 2048: a real signal, one channel per transducer. (A)
- Maximum signal of a ping = max(max − 2048, 2047 − min)/2047. (A)

## 7. Older revisions (7 and 10)

| Record | rev ≥ 12/13 | rev 8–10 | rev 6–7 |
|---|---|---|---|
| HEAD_SETTINGS | complete (§5) | without items 16–28 | second name in a fixed 16-byte field; TVG **without** ×0.001; without items 16–28; the 3rd u32 is unused; gain table of **128 floats with no count** (all ≤ 0 → 1.0) |
| Transducer | 17 bytes | 15 bytes (no last u16) | 15 bytes |
| Orientation | subtype 6 per transducer | one 52-byte record, no per-transducer orientation | same |
| Sonar | §5 | same as rev 13 | rev 7: u16 type, 12 bytes, serial in a fixed 16-byte field, u16 firmware, u16 |
| Ping info | with ranges | with ranges (rev > 7) | **no ranges**: the head range is used |
| Signal | I and Q channels | same | **a single channel** |

Heads without per-transducer orientation use the older sector layout of `IMAGING.md` §3.5.

## 8. Still open
- The C fields of HEAD_SETTINGS, the transducer and the orientation.
- Index types 3–5 and ping records 0x14/0x16/0x17.
- Block 0 fields 0x08–0x17.

## 9. Writing (`src/opensonar/writer.py`)
opensonar writes **rev 14**:

1. Block 0: header (§3) with 5 indices.
2. Configuration from block 1: features (28 zero bytes), sonar, and for each head the HEAD_SETTINGS,
   orientations 1 and 2 and, for each transducer, its record followed by orientation 6 (or 5); then the
   end record.
3. One chain per ping, in consecutive blocks, in arrival order: 16-byte header (time of writing, local
   time zone, order in the file), info, orientations 3 and 4, one signal per transducer (mode 2, XOR
   0x4C + zlib; empty Q channel if none), end.
4. Indices 1 to 5, one block each, at the end of the file.

- Each chain uses the fewest blocks that fit it; the rest of the last block gets `u16 rev, u16 0x1F` and
  zeros.
- Each block: u32 next (0 on the last), u32 previous (0 on the first).

`tests/test_writer.py` copies the 12 sample files (rev 7–14) to rev 14 and reads them back with the same
fields, signals, metadata and images. A recording made live (emulator → driver → `SonWriter`) gives
the same images as its source.
