# Current status (v4.1.0)

## Done
- **`.son` reader** (`son.py`): revisions 7, 10, 13 and 14 (`SON_FORMAT.md`).
- **Images** (`image.py`, `IMAGING.md`): R-Theta and XY, for heads with and without per-transducer
  orientation; geometric map cached per configuration and range (0.04–0.08 s per ping).
- **Network protocol** (`PROTOCOL.md`) and **native driver** (`net.py`): discovery, connection,
  range/gain/TVG, pings (plain, I/Q, compressed), clock correction.
- **Emulator** (`tools/sonar_emulator.py`): the driver renders images identical to the source `.son`.
- **API** (`sdk.py`, `API.md`): `opensonar.open(source)` for `.son`, `ip[:port]` or discovery.
- **Writing** (`writer.py`): rev 14 `.son`.
- **Viewer** (`viewer.py`, `process.py`) and **CLI** (`export`, `live`, `discover`, `server`, `ctl`,
  `gui`, `network`).
- **Service** (`server.py`, `client.py`, `SERVER.md`): ZeroMQ REP 4545 / PUB 4546, recording,
  `opensonar ctl`, `examples/subscriber.py`.
- **GUI** (`gui.py`, `display.py`, `GUI.md`, `DISPLAY.md`): discovery, connection, playback, heads side
  by side, settings, colour mapping, cursor/ruler, recording in several formats, event marks.
- **Formats**: `.son`, MP4, NPY, NPZ, PNG, TIFF with `index.csv`.
- **Time** (`SYNC.md`): corrected UTC everywhere, also with a sonar clock in 1970.
- **Packages** (`packaging/`, `INSTALL.md`): .deb (Ubuntu 22.04/24.04) and AppImage, validated on the
  real PC.
- **ROS 2, stage 1** (`ros2/`): `opensonar_msgs` and `opensonar_ros/sonar_node`, tested with the real
  sonar.
- **Common `Frame` layer** (`frame.py`): beamformed intensities on a range × beam angle grid; every
  family ends up there, and the XY fan is built from any frame.
- **`.aris` recordings** (`formats/aris.py`, `ARIS_FORMAT.md`): read and write, per-lens beam angles;
  checked on the published sample recording.
- **`.aris` live driver** (`aris_net.py`, `ARIS_LIVE.md`) and its **emulator** (`tools/aris_emulator.py`):
  beacon discovery, protobuf commands, frame parts and reordering, valid-settings rules, the service as
  the only client, PING every second, automatic reconnection, busy sonar refused.
- **`.ddf` recordings** (`formats/ddf.py`, `DDF_FORMAT.md`): version 3 read, lens beam model, UTC times,
  14° vertical aperture; checked on public recordings.
- **Integration**: the GUI (type selector, free/busy in discovery, no-frames notice), service,
  export and ROS 2 (`BeamFrame` topic, optional simulator imaging-sonar output) work with every family.
- **Network profile**: 192.168.1.10/24 + 169.254.10.10/16 on the same card; ufw rules for UDP 56124
  and 56125 when ufw is active.
- **Tests**: 131 (`python -m pytest -q tests`).

## Real sonar (M900/2250-130, 2026-10-13/14)
Discovery, connection (6 heads), range/gain/TVG, recording at the full rate (~19 pings/s with two
heads), through the driver, the service, the GUI and ROS 2. Only tested in a bucket so far (noise).

## Open
- **Images with real echoes** (FURG tank).
- **`.aris` on a real sonar**: driver, timing rules and beam orientation so far only against the emulator
  and the sample recording.
- **`.ddf`**: version 4, 48 beams and the long-range lens have no sample files; the `reverse` flag of
  the file header is not interpreted; beam left/right not checked on a known scene.
- 3D reconstruction: vehicle pose per frame, using `vertical_fov` (known for `.ddf`, not for `.aris`).
- ROS 2 stage 2: ProjectedSonarImage, live parameters, services, TF, replay `/clock`, RViz,
  launch_testing.
- The multicast ping path and the status/trigger messages (`PROTOCOL.md` §9).
- DP900-90 R-Theta (0.989): likely rounding at the boundaries between its 4 sectors.
- Revisions 8, 9, 11 and 12 and real I/Q signals have no samples.
