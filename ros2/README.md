# opensonar for ROS 2 (Humble, Ubuntu 22.04)

Two packages:

| Package | Type | What |
|---|---|---|
| `opensonar_msgs` | ament_cmake | `RawPing`, `BeamFrame`, `SonarInfo`, `SonarStatus`, `EventMark`, `TransducerConfig` |
| `opensonar_ros` | ament_python | `sonar_node` (publishes the sonar) |

`sonar_node` is a **client of the opensonar service** (`opensonar server`, [`../docs/SERVER.md`](../docs/SERVER.md)):
the service owns the sonar, and the node turns its ZeroMQ messages into ROS topics. If no service is
running, the node starts one, and stops it when the node exits. The node needs only `python3-zmq` and
`numpy`, not the opensonar library: the ROS Python (3.10) is not the Python bundled in the opensonar `.deb`.

> **Status:** stage 1. Image, raw, info, status, events and diagnostics, tested with
> the emulator. Next: R-Theta as `marine_acoustic_msgs/ProjectedSonarImage`, parameters changing
> while running, the start/stop/record/mark services, TF, replay with `/clock`, RViz config and
> launch_testing tests.

## Build

```sh
sudo apt install python3-zmq                     # once
sudo apt install ./opensonar_<version>_amd64.deb   # the opensonar service (puts `opensonar` on the PATH)
conda deactivate                                 # IMPORTANT: no conda/venv active (see below)
source /opt/ros/humble/setup.bash
cd ros2 && colcon build --symlink-install
source install/setup.bash
```

**Build and run without conda (or any venv) active.** ROS 2 Humble uses the system Python 3.10. With
conda active, CMake picks conda's Python and the message generation fails (`No module named 'em'`),
and the node may not find `rclpy`. Check with `which python3`, which must say `/usr/bin/python3`. To
stop conda activating itself: `conda config --set auto_activate_base false`. For a single command
without touching the shell: `env -i HOME=$HOME PATH=/usr/bin:/bin bash -c 'source /opt/ros/humble/setup.bash; …'`.

## Run

**Real sonar** (network set up with the GUI's *Configure sonar network*, or `opensonar network`):
```sh
ros2 launch opensonar_ros sonar.launch.py source:=192.168.1.45
ros2 launch opensonar_ros sonar.launch.py params:=my_sonar.yaml       # heads, range, gain, TVG, topics…
```

**Emulator** (no sonar; from the repository, with the development venv):
```sh
.venv/bin/python tools/sonar_emulator.py samples/exp1.son --compress &       # a P900-130 on port 1149
ros2 launch opensonar_ros sonar.launch.py source:=127.0.0.1:1149
```
To use the repository's service instead of the installed one, set
`server_command: "/path/to/opensonar/.venv/bin/opensonar server"` in the parameter file.

**Replay of a `.son` file:** `source:=/path/recording.son` already publishes the file at the recorded
pace, with the recorded timestamps. Publishing `/clock` for `use_sim_time` comes in the next stage.

Several programs can share the sonar: the GUI and the node can both be clients of the same service.
If the node finds a service already connected to the same source, it reuses it, and on exit it
only stops pinging (it does not close someone else's service).

### Simulator imaging-sonar output
Set `sim_imaging_sonar_type` to the simulator's message type (`"<package>/msg/<Type>"`). If it is empty,
or that package is not installed in the environment, the node runs without this topic (a warning says
so). The message must have these fields:

| Field | Filled with |
|---|---|
| `header` | ping time (UTC) and the head frame, as on the other topics |
| `bins_range`, `bins_azimuth` | the size of this ping's R-Theta image (they change with the range) |
| `raw_image` | `sensor_msgs/Image` 32FC1, 0–1 (u8 frames / 255, u16 images / 65535); rows = range, row 0 the nearest; columns = azimuth, **column 0 the right-most (starboard) beam**, as the simulator fills them (its azimuth is positive to the left and the columns start at the minimum) |
| `intensity`, `elevation`, `point_cloud` | **empty**: they are the simulator's ground truth, which a real sonar does not have |

### ROS domain
The node uses the domain of its environment (`ROS_DOMAIN_ID`, default 0); it does not set one. To be
seen by other nodes, such as a simulation that consumes the sonar topics, it must run in the same
domain as them:
```sh
export ROS_DOMAIN_ID=7          # the same value the simulation uses
ros2 launch opensonar_ros sonar.launch.py source:=192.168.1.45
```
`ros2 topic echo`/`ros2 topic list` also only see the domain they run in, and the `ros2` daemon keeps
the domain it started with: after changing it, run `ros2 daemon stop`. A busy domain (another
simulation publishing a lot) can make those commands slow; a separate domain avoids that when testing.

## Topics

Under the node's namespace (default `/sonar`), for each head N in `heads`:

| Topic | Type | QoS | Content |
|---|---|---|---|
| `headN/image` | `sensor_msgs/Image` `mono16` | best effort, depth 5 | XY fan, cropped to the range; row 0 = farthest, the sonar at the bottom centre |
| `headN/raw` | `opensonar_msgs/RawPing` | reliable, depth 200 | P900/M900: raw 12-bit signal of every transducer (+ Q), bins/resolution of this ping, compact configuration |
| `headN/sim_imaging_sonar` | the type in `sim_imaging_sonar_type` | reliable | optional, both families: the ping in a simulator's imaging-sonar message, so a digital twin takes real and simulated sonars the same way (below) |
| `headN/frame` | `opensonar_msgs/BeamFrame` | reliable, depth 200 | `.aris` family (instead of `raw`): u8 intensities, samples × beams (row 0 = nearest), range start and step, beam angles (+ left) |
| `headN/info` | `opensonar_msgs/SonarInfo` | reliable, **transient_local** | head description, published on connect and when range, gain, TVG, sound speed or the ping rate (> 10 %) change |
| `status` | `opensonar_msgs/SonarStatus` | depth 10, 1 Hz | connection, pinging, published/received/lost pings, recording, clock offset, error |
| `events` | `opensonar_msgs/EventMark` | depth 50 | event marks (GUI ⚑ Mark, `opensonar ctl mark`) |
| `/diagnostics` | `diagnostic_msgs/DiagnosticArray` | 1 Hz | `connection` (OK/WARN/ERROR + ping rate per head), `lost pings`, `sonar clock` (WARN when the sonar clock was never set) |

- **`header.stamp`** = the ping's UTC time, corrected for the sonar clock: the PC clock
  ([`../docs/SYNC.md`](../docs/SYNC.md)). Checked against the emulator: within 1 ms of the recorded
  time (the protocol rounds to ms).
- **Tested with the real sonar** (M900/2250-130, heads 0 + 3 at 10 m, 2026-10-14): `raw` ~6 Hz per
  head (12 in total), 0 lost, increasing ping numbers, stamps in current UTC although the sonar clock
  is in 1970 (the `sonar clock` diagnostic warns about it). From ping to ROS takes 0.1–0.4 s: most of
  it is the service rendering the XY image before publishing the ping. `image` comes at ~2 Hz per head,
  because a 10 m fan of the 2.25 MHz head is 1238×2304 pixels; the raw pings carry every ping.
- **`frame_id`** = `<frame_prefix>N` (default `sonar_head0`, `sonar_head3`…). The static TF to
  `base_link` is part of the next stage; until then, publish it with `static_transform_publisher`.
- The **bins and the resolution** (`range_bins`, `range_resolution`, `bin_range_near/far`) are in
  every `RawPing`, because they change with the range, and also in `SonarInfo`.
- **Beam angles** in `SonarInfo.beam_angles` follow REP-103: radians, positive to the **left**
  (counter-clockwise seen from above). opensonar itself uses positive to the right; the node flips the
  sign.

### Reprocessing a RawPing
`RawPing` carries the signal and a compact configuration. The big part (chirp replica, per-beam gains)
is in `SonarInfo` with the same `config_id`, published once (transient_local), so a bag does not
repeat 60–155 KB per ping. With the opensonar library:
```python
from opensonar import image
from opensonar.son import Ping, config_from_dict
cfg = config_from_dict({"model": info.head_model, "center_freq": info.center_freq, "sound_speed": info.sound_speed,
    "range_min": info.range_min_limit, "range_max": info.range_max_limit, "sample_rate": info.sample_rate,
    "pulse_sample_rate": info.pulse_sample_rate, "element_spacing": info.element_spacing,
    "transducers": [dict(index=t.index, high_freq=t.high_freq, low_freq=t.low_freq, flags=t.flags,
                         position=list(t.position), rotation=list(t.rotation), oriented=t.oriented) for t in info.transducers],
    "pulse": np.array(info.pulse_real) + 1j * np.array(info.pulse_imag), "normalization": info.normalization})
splits = np.cumsum(raw.samples)[:-1]
ping = Ping(raw.number, stamp_seconds, raw.tz_offset, raw.range_start, raw.range_stop,
            np.split(np.array(raw.signal, np.uint16), splits), [])
fan, geo = image.xy(cfg, ping)       # the same image as the service
```
The service's own test checks that this rebuilds identical XY and R-Theta images.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `source` | `192.168.1.45` | sonar IP, `ip:port` (emulator), or a `.son` / `.aris` path |
| `family` | `""` | `"aris"` for a live sonar of the `.aris` family (also `family:=aris` in the launch file) |
| `system_type` | `-1` | `.aris` family: 0, 1 or 2; < 0 = from the sonar beacon |
| `heads` | `[0]` | heads to ping (in turn) and publish |
| `range` | `[0.0]` | `[stop]` or `[start, stop]` in m for every head; `[0.0]` keeps the sonar's |
| `gain`, `tvg` | `-1.0` | < 0 keeps the sonar's |
| `frame_prefix` | `sonar_head` | frame of head N |
| `smooth` | `true` | 3×3 mean on the images |
| `replay_speed` | `1.0` | `.son` / `.aris` source: 1 = recorded pace (with the recorded stamps), 2 = twice as fast, 0 = as fast as possible |
| `publish_image`, `publish_raw`, `publish_info`, `publish_diagnostics` | `true` | which topics |
| `image_qos_depth`, `raw_qos_depth` | 5, 200 | history depths |
| `service_host`, `rep_port`, `pub_port` | `127.0.0.1`, 4545, 4546 | the opensonar service |
| `start_service`, `server_command` | `true`, `opensonar server` | start the service if none answers |

## Recording with rosbag2, together with the ROV

Record the raw pings (reliable, every ping) and the info, plus the vehicle's navigation and TF. Images
can be made again from the raw pings, so recording them is optional:
```sh
ros2 bag record -o dive_$(date -u +%Y%m%dT%H%M%SZ) \
    /sonar/head0/raw /sonar/head0/info /sonar/events /sonar/status /diagnostics \
    /tf /tf_static /mavros/local_position/odom /mavros/imu/data /dvl/data \
    --qos-profile-overrides-path info_qos.yaml
```
`info_qos.yaml` makes the recorder take the transient_local `info` even if it was published before
the recording started:
```yaml
/sonar/head0/info:
  reliability: reliable
  durability: transient_local
  history: keep_last
  depth: 1
```
For the stamps to line up with the ROV's topics, synchronise the clocks first (chrony/PTP,
[`../docs/SYNC.md`](../docs/SYNC.md)). The sonar stamps are the PC's UTC time. Use **⚑ Mark** (GUI)
or `opensonar ctl mark "text"` at a visible event; it shows up on `/sonar/events` in the bag.
