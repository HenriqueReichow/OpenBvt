# Live acquisition — checklist for the test with the real sonar

Goal: images with **real echoes** from the sonar over the network (`opensonar live`,
`src/opensonar/net.py`), and a traffic capture (`tools/capture_net.sh`) to settle what
`docs/PROTOCOL.md` still marks as open.

## Already validated
- Against the emulator (`tools/sonar_emulator.py`), the driver renders images **identical** to the source
  `.son` for MB2250-45, P900-130 and M900-130, with plain and compressed signals. It also decodes I/Q,
  adjusts range, gain and TVG, corrects the clock and finds the emulator through discovery
  (`tests/test_net.py`).
- Real sonar, 2026-10-14 (192.168.1.45, M900/2250-130, 6 heads), with capture:
  - `opensonar discover` found it (multicast 239.168.1.45:1149 announced).
  - Connection with 6 heads; the sonar clock was ~1.79·10⁹ s behind (near 1970) and the driver
    corrected it. Full XML in `samples/live/sonar_config_20261014.xml`.
  - `opensonar live --head 0 --range 5 --record`: 2914 pings in 187 s (~15.6 pings/s, 227 MB `.son`),
    range changed live, window and recording without errors.
  - The sonar was in a **shallow bucket**: noise only, plus the bucket walls in the first ~0.5 m.
  - While the sonar is on the network, `tests/test_sdk.py::TestDiscovery` finds it instead of the
    emulator and fails (expected).
- **Not tested yet**: images with real echoes (FURG tank).

## Before going (at home)
1. Capture permission without `sudo` (once; then log out and back in):
   ```sh
   sudo usermod -aG wireshark $USER
   ```
2. Test suite: `python -m pytest -q tests` (all pass).
3. Dry run with the emulator:
   ```sh
   python tools/sonar_emulator.py samples/exp1.son --compress &     # TCP 1149 + discovery
   opensonar discover
   opensonar live 127.0.0.1 --range 3 --pings 20
   kill %1
   ```

## On the day

### 1. Sonar in the water
- The transducer face must be **submerged**; out of the water the image is uniform noise.
- Leave a target at a known distance (tank wall, pipe, bottom).
- Power the PoE on and wait ~40 s for the sonar to boot.

### 2. Network
The GUI's **Configure sonar network** button (or `opensonar network`) does it. By hand:
```sh
nmcli device disconnect enp4s0
sudo ip addr flush dev enp4s0
sudo ip addr add 192.168.1.10/24 dev enp4s0
sudo ip link set enp4s0 up
ping -c 3 192.168.1.45
```
If the Wi-Fi is on the same subnet, turn it off (`nmcli radio wifi off`).

### 3. Capture (terminal 1) — start BEFORE connecting
```sh
tools/capture_net.sh enp4s0 192.168.1.45
```
Saves `vendor/capture/sonar_YYYYMMDD_HHMMSS.pcapng` until Ctrl+C. Write down the times.

### 4. Sessions (terminal 2)
```sh
opensonar discover                                                      # A: discovery
opensonar live 192.168.1.45 --pings 1 --no-window                       # B: connection + 1 ping
opensonar live 192.168.1.45 --range 5 --pings 50 --npz samples/live/h0_5m           # C: head 0 (2.25 MHz), 5 m
opensonar live 192.168.1.45 --head 3 --range 20 --npz samples/live/h3_20m           # D: head 3 (900 kHz)
opensonar live 192.168.1.45 --range 5 --gain 5 --tvg 0.5 --pings 20                 # E: settings
```
In the window: space pauses, ↑/↓ change the range (±20 %), `c` colours, `l` log/linear, `g` grid,
`s` saves the fan (16-bit PNG), `q` quits. Or use the GUI (`opensonar gui`, `docs/GUI.md`).

### 5. What to check
- [ ] `discover` lists 192.168.1.45:1149.
- [ ] The 6 heads appear with their models and ranges.
- [ ] The target appears at the right distance; moving it moves the blob.
- [ ] `--range` changes the end of the fan; `--gain` and `--tvg` change the brightness.
- [ ] Ping rate and no error in 1 min.
- [ ] Capture growing; in Wireshark, check whether any ping comes over UDP 239.168.1.45:1149.
- [ ] Write down the time of each session and the target distances.

## If something fails
- `error: cannot connect to the sonar at …`: network, PoE, or the sonar is still booting.
- `expected <Sonar>` or `head N without <Pulse>`: keep the capture and the message.
- Noise only: face out of the water, no target in range, or low gain.

## `.aris` sonar — checklist for the first real test
The driver was only run against `tools/aris_emulator.py` (`docs/ARIS_LIVE.md`). On the first real sonar:
1. Network: the `opensonar-net` profile (169.254.10.10/16) or DHCP; `opensonar discover` (or **Discover
   sonars**) must list it as *free*. If it is *busy*, close the other program.
2. Connect from the GUI (type **.aris sonar**). Without frames after ~5 s the status bar says so: the
   settings were probably refused (check `aris_net.DEFAULTS` for that model).
3. Range and gain: the window in the frames must follow `adjust_range` (`window()` vs the frame ranges).
4. Orientation: a target on the right of the sonar must appear on the right of the fan (beam 0 = right-most).
5. Time: compare the frame time with the PC clock (`docs/SYNC.md`).
6. Unplug the cable for a few seconds: the status bar must report the reconnection, and frames must
   come back without restarting anything.
7. Capture the traffic (`tools/capture_net.sh`) to keep a reference of the real protocol.
