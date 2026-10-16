# opensonar — open software for imaging sonars

## Goal
Open software, on Linux, for imaging sonars, aimed at 3D reconstruction. Families: P900/M900 (raw
signal, beamformed here; `.son` read/write, live), `.aris` (lens; read/write, live) and `.ddf` (lens;
read). Every family ends up in the common `Frame`; render, record, share the pings (service, GUI, ROS 2).

## Status (v4.1.0) — see docs/STATUS.md
- `.son` reader/writer, R-Theta and XY images, native network driver, emulator, API, ZeroMQ service,
  GUI, recording/export in several formats, corrected UTC, .deb/AppImage, ROS 2.
- Common `Frame` layer; `.aris` reader/writer, live driver (only client, keepalive, reconnection, busy
  refusal) and emulator; `.ddf` version 3 reader; optional simulator imaging-sonar output in ROS 2.
- Validated with a real M900/2250 (in a bucket); `.aris` against the sample file and the emulator; `.ddf`
  against public recordings.
- **Still to do**: images with real echoes (FURG tank); `.aris` on a real sonar; `.ddf` v4/48 beams/LR;
  3D reconstruction; ROS 2 stages 2+.

## Repository layout
- `src/opensonar/`:
  - `son.py` (reader) and `image.py` (R-Theta/XY, with the map cache).
  - `frame.py` (common `Frame`, XY of any frame, `render`).
  - `formats/aris.py` (.aris reader/writer, beam tables, reordering) and `formats/ddf.py` (.ddf reader).
  - `net.py` (P900/M900 protocol, codec, driver, discovery), `aris_net.py` (.aris live driver) and
    `sdk.py` (API: `open`, `Sonar`, `Head`, `FrameHead`, `Stream`, `FILE_FORMATS`).
  - `writer.py` (`SonWriter`, `copy_son`, `raw_writer`); `netsetup.py` (network profile).
  - `viewer.py` (window) and `process.py` (acquisition in a separate process).
  - `server.py` (ZeroMQ service) and `client.py` (`Client`, `Subscriber`).
  - `media.py` (image formats with index.csv, MP4, export).
  - `display.py` (colour mapping) and `gui.py` (PySide6 + pyqtgraph GUI, a client of the service).
  - `cli.py`: `export`, `live`, `discover`, `server`, `ctl`, `gui`, `network`.
- `tests/`: `test_basic.py`, `test_aris*.py`, `test_ddf.py` and `test_sim_imaging_sonar.py` run without
  data (synthetic files); the others are skipped without `samples/` (`exp1.son`, `sample.aris`, `ddf/*.ddf`).
- `docs/`: `SON_FORMAT.md`, `ARIS_FORMAT.md`, `ARIS_LIVE.md`, `DDF_FORMAT.md`, `IMAGING.md`,
  `PROTOCOL.md`, `API.md`, `LIVE.md`, `GUI.md`, `SYNC.md`, `INSTALL.md`, `SERVER.md`, `DISPLAY.md`, `STATUS.md`.
- `tools/`: `sonar_emulator.py FILE.son [--compress] [--iq]` (emulated P900/M900), `aris_emulator.py
  FILE.aris` (emulated .aris sonar, one client, `drop()`), `capture_net.sh IFACE [IP]` (pcapng capture
  into `vendor/capture/`).
- `ros2/`: `opensonar_msgs` and `opensonar_ros` (build without conda, see `ros2/README.md`).
- `packaging/`: `build.sh` (→ `dist/` .deb + AppImage), container tests, desktop entry, icon, systemd
  unit, polkit rule.
- Not in git: `vendor/`, `samples/`, `build/`, `dist/`.

## Tools
- Tests: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q tests` (`tests/conftest.py` loads
  pytest-qt offscreen; the ROS plugins break pytest on this machine).

## Rules
- Nothing in the repository may name a sonar manufacturer or look like a copy of a vendor SDK: no
  vendor code, headers, symbol names or addresses, and no tools that run vendor binaries. Everything is
  described as our own specification and reimplemented. Modules and docs are named after the file
  format (`formats/aris.py`, `formats/ddf.py`); model names only where technically needed. The only
  exception: data reused from MIT-licensed sources (beam tables, lens model) carries its license notice
  in `THIRD_PARTY_NOTICES.md`, and only there.
- No simulator or private-project names in the repository (generic names, e.g. `sim_imaging_sonar`).
- Before committing, search the tree for manufacturer, product and tool names (only
  `THIRD_PARTY_NOTICES.md` may contain them).
- The repository is public: ask before every push.
- NEVER commit `vendor/` or `samples/`.
- Do not claim anything without evidence; if unsure, mark it as a hypothesis.
- One stage at a time. When each one is done, stop and show the result.
- Everything in the repository (code, identifiers, comments, docs, commit messages) is in English.
- Commits carry no Co-Authored-By line.
- Answer the user in Portuguese.
