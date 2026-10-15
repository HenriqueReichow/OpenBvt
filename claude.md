# opensonar — open software for imaging sonars

## Goal
Open software, on Linux, for imaging sonars, aimed at 3D reconstruction. For now it supports the P900
and M900: read and write `.son`, render the images, acquire live over the network, share the pings
(service, GUI, ROS 2).

## Status (v4.1.0) — see docs/STATUS.md
- `.son` reader/writer, R-Theta and XY images, native network driver, emulator, API, ZeroMQ service,
  GUI, recording/export in several formats, corrected UTC, .deb/AppImage, ROS 2 stage 1.
- Validated with a real M900/2250 (in a bucket).
- **Still to do**: images with real echoes (FURG tank); support for more sonar types; ROS 2 stages 2+.

## Repository layout
- `src/opensonar/`:
  - `son.py` (reader) and `image.py` (R-Theta/XY, with the map cache).
  - `net.py` (protocol, codec, driver, discovery) and `sdk.py` (API: `open`, `Sonar`, `Head`, `Stream`).
  - `writer.py` (`SonWriter`, `copy_son`).
  - `viewer.py` (window) and `process.py` (acquisition in a separate process).
  - `server.py` (ZeroMQ service) and `client.py` (`Client`, `Subscriber`).
  - `media.py` (image formats with index.csv, MP4, export).
  - `display.py` (colour mapping) and `gui.py` (PySide6 + pyqtgraph GUI, a client of the service).
  - `cli.py`: `export`, `live`, `discover`, `server`, `ctl`, `gui`, `network`.
- `tests/`: `test_basic.py` runs without data; the others are skipped without `samples/`.
- `docs/`: `SON_FORMAT.md`, `IMAGING.md`, `PROTOCOL.md`, `API.md`, `LIVE.md`, `GUI.md`, `SYNC.md`,
  `INSTALL.md`, `SERVER.md`, `DISPLAY.md`, `STATUS.md`.
- `tools/`: `sonar_emulator.py FILE.son [--compress] [--iq]` (emulated sonar), `capture_net.sh IFACE [IP]`
  (pcapng capture into `vendor/capture/`).
- `ros2/`: `opensonar_msgs` and `opensonar_ros` (build without conda, see `ros2/README.md`).
- `packaging/`: `build.sh` (→ `dist/` .deb + AppImage), container tests, desktop entry, icon, systemd
  unit, polkit rule.
- Not in git: `vendor/`, `samples/`, `build/`, `dist/`.

## Tools
- Tests: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q tests` (`tests/conftest.py` loads
  pytest-qt offscreen; the ROS plugins break pytest on this machine).

## Rules
- Nothing in the repository may name a sonar manufacturer or look like a copy of a vendor SDK: no
  vendor code, headers, tables, symbol names or addresses, and no tools that run vendor binaries.
  Everything is described as our own specification and reimplemented.
- NEVER commit `vendor/` or `samples/`.
- Do not claim anything without evidence; if unsure, mark it as a hypothesis.
- One stage at a time. When each one is done, stop and show the result.
- Everything in the repository (code, identifiers, comments, docs, commit messages) is in English.
- Commits carry no Co-Authored-By line.
- Answer the user in Portuguese.
