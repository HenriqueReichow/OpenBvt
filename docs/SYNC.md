# Time synchronisation (sonar, PC, ROV)

## Where the ping time comes from
1. The sonar stamps each ping with **its own clock**. P900 and M900 sonars are often never set: the
   M900/2250 tested on 2026-10-14 reports a date near **1970** (offset ≈ −1.79·10⁹ s, about −20 740
   days).
2. On connection, the driver sends a TimeRequest with the PC time and gets the sonar time back. The
   offset is `sonar − midpoint of the round trip` (~1 ms precision, `PROTOCOL.md` §4.4).
3. Every ping time is corrected by that offset. **From then on, ping times follow the PC clock**, in
   UTC.

So the time in **everything opensonar writes and publishes is the PC's UTC time**:

| Output | Field | Clock |
|---|---|---|
| `.son` (rev 14) | ping time (s + ms) and the time zone of the PC | PC, UTC (corrected) |
| `index.csv` of NPY/NPZ/PNG/TIFF | `utc`, `timestamp` | PC, UTC (corrected) |
| `.events.csv` and the `event/<serial>` topic | `utc`, `timestamp` | PC, UTC, at the moment of the mark |
| ping messages of the service | `timestamp`, `utc` | PC, UTC (corrected) |
| MP4 video | real-time frames (no time inside) | starts when recording starts |
| GUI status bar | "UTC hh:mm:ss.mmm", "sonar clock …" | the last ping / the measured offset |

Checked with the real sonar (clock in 1970: `.son` and `index.csv` at 2026-10-14T16:46:02Z) and by
`tests/test_server.py::test_utc_is_corrected_when_the_sonar_clock_is_in_1970` (emulated sonar with the
same offset: `.son`, `index.csv` and `.events.csv`).

**Limits:**
- The offset is measured **once per connection**. If the sonar clock drifts against the PC during a
  long session, the error grows with it (a typical crystal drifts 10–50 ppm, so 0.04–0.2 s per hour).
  Reconnecting measures it again. A periodic re-measurement is a possible improvement.
- The ping time is when the sonar stamped it, plus the offset, with no correction for the network or
  processing delay. On a direct cable the delay is ~1–2 ms.
- If the PC clock is wrong, every time is wrong in the same way. So **synchronise the PC with the
  ROV** (below).

Never set the sonar clock to fix this: the correction already does it, and opensonar does not write the
sonar's clock.

## Synchronising the PC with the ROV

Goal: the PC running opensonar and the ROV computer (or the vehicle's navigation, DVL, cameras) share
one clock, so the ping times line up with the ROV's logs and ROS topics.

### Option 1 — NTP with chrony (simple, ~1 ms on a LAN)
Use one machine as the reference: the ROV computer if it has GPS/PPS, otherwise the topside PC.

On the **reference** (server), `/etc/chrony/chrony.conf`:
```
# keep the normal internet servers if any; and also serve time to the vehicle network
allow 192.168.2.0/24          # the ROV network (not the sonar's 192.168.1.0/24, unless it is the same)
local stratum 10              # keep serving even without internet
```
On the **other machine** (client):
```
server 192.168.2.1 iburst prefer   # the reference's IP
makestep 1 3                       # allow a jump at start if the clock is far off
```
Then on both: `sudo systemctl restart chrony`. Check with `chronyc tracking` ("System time" offset
under 1 ms) and `chronyc sources -v` (`^*` marks the selected source).

### Option 2 — PTP (IEEE 1588, sub-microsecond with hardware timestamping)
For when the ROV already uses PTP (cameras, multibeam) or when NTP is not precise enough.
```sh
sudo apt install linuxptp
ethtool -T eth0                                      # "hardware-transmit/receive" = hardware PTP support
# grandmaster (ROV or topside):
sudo ptp4l -i eth0 -m                                # add -S for software timestamping if no hardware support
# client (the other machine):
sudo ptp4l -i eth0 -s -m                             # -s: client only
sudo phc2sys -s eth0 -c CLOCK_REALTIME -w -m         # copies the NIC clock to the system clock
```
With `ptp4l` on the NIC and `phc2sys` to the system clock, `CLOCK_REALTIME` (the one opensonar uses)
follows the grandmaster. For permanent use, enable the `ptp4l@eth0` and `phc2sys@eth0` systemd units.
If the grandmaster has GPS/PPS, the whole system is in absolute UTC.

### Option 3 — GPS/PPS directly on the PC
`gpsd` + chrony with `refclock SHM 0` and `refclock PPS /dev/pps0` gives absolute UTC with ~1 µs
(PPS) on the PC itself. Some sonars have their own PPS
input; opensonar does not use it, since the sonar time is replaced by the PC time anyway.

### Checking it with the ROV
1. Synchronise (option 1 or 2) and check the offset (`chronyc tracking`, or the `ptp4l` "master offset").
2. Record with **⚑ Mark** at a visible event (for example a light switched on in front of the camera
   and the sonar, or an object dropped in the water). The mark's `utc` and the ping times can be
   compared with the ROV log of the same event.
3. In ROS 2, use the ping `timestamp` (UTC, corrected) as `header.stamp`. With synchronised clocks
   it lines up with the other topics.
