# Image formation

How opensonar turns the raw signal of a ping into the R-Theta and XY images (`src/opensonar/image.py`).

## 1. Principle: frequency steering
These sonars use frequency-steered arrays, as described in the public patents US 7 606 114,
US 8 811 120 and US 8 964 507 ("Systems and methods implementing frequency-steered acoustic arrays for
2D and 3D imaging"):

- **Frequency sets the direction.** The array is periodic, with phase-shifted elements. Like a
  diffraction grating, each frequency leaves at a different angle. For a ¼ λ array with element spacing
  `d`, the frequency leaving at angle θ is **f = c / (4 d sin θ)**.
- **Wideband pulse.** Each transducer sends a chirp that covers its band, so each slice of frequency
  lights one angle.
- **Time-frequency reception.** A short-time Fourier transform of the echo gives range (time) against
  angle (frequency).
- **Several arrays.** Each transducer covers one angular sector. Mounting them at different angles
  joins the sectors into a wide fan.

## 2. Inputs (all from the `.son` or the network configuration)
- Signal of each transducer: u16, 12 bits, zero at 2048.
- Sampling rate `fs` (HEAD_SETTINGS item 12) and reference clock (item 13).
- Element spacing `d` (item 5), sound speed `c`, ping range `[start, stop]`, head range limits.
- Per transducer: upper and lower frequency of the sector, orientation flags, position (x, y) and Z
  rotation.
- Pulse replica (complex table) and per-beam gain table.

## 3. Pipeline

### 3.1 Frames
- Hop between frames, from the range span L = stop − start: 25 samples if L ≤ 5 m, 166 if L ≥ 100 m,
  otherwise int(256 · (0.1 + 0.55 · (L − 5)/95)). The constants are kept in float32, so the result
  is the same number for the same configuration.
- Frames = samples // hop; frame rate = fs / hop.
- A **recorded** ping shorter than the range is padded with 2048 up to floor((stop − start) · 2/c · fs)
  samples. A **live** ping is used as it comes, not padded: checked against the reference software
  through the emulator (M900-130 at 1.0–4.9 m: 6089 samples, 243 rows live; the same ping read back
  from a file is padded, as the recordings are). Open: in that live case the rows start one row
  (1.6 cm) away from the reference (frame shift 31 against 30, start × frame rate = 30.96); with
  recordings the frame shift matches the reference exactly.

### 3.2 Spectrogram per transducer
- Input value = 2048 − sample.
- **Hann window** of 256 points without the zero end points: w[i] = ½(1 − cos(2πi/257)), i = 1…256.
- 256-point FFT; **magnitude** |X| of bins 1…128 (128 beams per sector).
- Each beam is multiplied by its **gain**: the per-beam table resampled to 128 values with a triangular
  kernel of half-width 2.5/M, normalized by the sum of the weights.
- Truncated to u16 (maximum 65 535).

### 3.3 Chirp delay per bin
Each frequency of the chirp leaves at a different instant, so each bin is shifted in time:
- Replica: the real part of the pulse table, one point every (clock / fs), with 128 zeros on each side.
- 256-point spectrogram of the replica with hop 1. For each bin, the delay is the frame of the peak
  minus the number of frames.
- Scale = (replica points / clock) / number of frames. Shift of the bin, in frames =
  round(delay · scale · frame rate).

### 3.4 Range of each row
- Start time (s) = 2 · start/c + 128/fs − replica points/clock.
- Resolution = (stop − start)/frames.
- Row r has range (H − 1 − r + round(start · frame rate) + 1) · resolution. Row 0 is the farthest.

### 3.5 Geometry: pixel → sector, beam, frame
For each pixel at range R and angle A, the point is (R sin A, R cos A). For each sector:
1. Move to the transducer frame: subtract its position, rotate by its Z rotation, and take the radius
   and the local angle atan2(x′, y′).
2. Sign: s = −1 for transducers with flags 1 or 0x11, +1 for the others.
3. Sector limits: θ = asin(c / (4 d f)) at the upper and lower frequency.
4. Inside the limits and the range: beam = 128 − round((f − fs/2)/(fs/256)) with
   f = c / (4 d sin(s · angle)); frame = round(radius/res) − round(start · frame rate).

The sectors are searched in a circle, starting at the last one (even rows) or the first one (odd rows),
and the starting point becomes the last sector found. A pixel takes up to 2 sectors.

**Weights in the overlap:** favoured beam = trunc(range_byte/256 · 128), with
range_byte = round(256 · radius/max_range); weight = 1/|beam − favoured| (1 when equal), normalized and
truncated to 32 levels.

**Older heads (no per-transducer orientation in the file):** the sectors are placed side by side and
centred on 0°. Each one is asin(c/(4 d f_low)) − asin(c/(4 d f_high)) wide. A pixel takes the first
sector that contains its angle, without blending. The local angle is the inner limit + (θ − θ₀) for flags
0/0x10 and the outer limit − (θ − θ₀) for flags 1/0x11.

### 3.6 Render and filter
- One sector: the value. Two sectors: Σ trunc(value · weight/32).
- **3×3 mean** (on by default): integer sum of the 9 neighbours ÷ 9, borders copied.

### 3.7 R-Theta and XY
- **R-Theta**: rows = frames, columns = a uniform angular grid over the whole fan.
- **XY**: the same map, with Cartesian pixels. The box covers the fans of every sector at the head's
  minimum and maximum range. Width and height = ceil(extent / resolution), with the R-Theta resolution.
  Origin column = trunc(−x_min/res), origin row = trunc(y_max/res); pixel (r, c) is at
  x = (c − origin_col) · res, y = (origin_row − r) · res.
- `crop=True` renders only the box inside the ping range (the same pixels, much faster at short range).

### 3.8 Map cache
The geometric map depends only on the head configuration and the ping range, never on the signal.
`image._map` keeps the last 4 geometries. The M900 fan at 44 m takes 0.08 s per ping instead of ~1 s.

## 4. Validation
The test suite compares the images with a set of reference images for 12 recordings (not in git),
by Pearson correlation in log:

| Model | Rev | R-Theta | XY |
|---|---|---|---|
| P900-130 | 13, 14 | 0.9991–0.9995 | 0.9990–0.9996 |
| M900-130 | 13 | 0.9998 | 0.9995 |
| MB2250-45 | 13 | 0.9992 | 0.9991 |
| P900-45 | 10 | 0.9979 | 0.9983 |
| DP900-90 | 10 | 0.9893 | 0.9960 |
| P450-45 | 7 | 1.0000 | 0.9985 |
| M2250-130 (real sonar) | 14 | — | 0.9992 |

About 2 % of the map entries differ by one beam or one frame at the sector edges (floating-point
rounding); the 3×3 mean spreads each one over 9 pixels.
