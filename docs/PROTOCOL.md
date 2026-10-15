# Network protocol of the P900 and M900 sonars

What the opensonar driver (`src/opensonar/net.py`) and the emulator (`tools/sonar_emulator.py`) speak.
It was checked against a real M900/2250-130 (2026-10-13/14) and against the emulator.

Status marks:
- **real** = seen working with the real sonar;
- **emu** = works with the emulator only;
- **open** = not used by opensonar, or not understood yet.

## 1. Overview

| Channel | Transport | Port | Use | Status |
|---|---|---|---|---|
| Control | TCP, client → sonar | **1149** | configuration, commands, pings on request | real |
| Discovery | UDP multicast **224.192.45.34** | 1149 | discovery request / response | real |
| Multicast pings | UDP multicast, group announced by the sonar (239.168.1.45:1149 on ours) | variable | pings split into packets ≤ 1500 B | open |

opensonar uses the TCP path for pings: it asks for one ping and reads the answer on the same connection.

## 2. Integers and blobs
- Integers are **big-endian**.
- A **blob** is a u32 size followed by that many bytes, with no terminator. Text is sent as a blob.
- Signal attachments are u16 **little-endian** when the XML says `ByteOrder="LITTLE"`, and big-endian
  otherwise.
- The pulse and normalization attachments are **big-endian f32**.

## 3. Messages

### 3.1 Header (14 bytes)

| Offset | Type | Field |
|---|---|---|
| 0 | u32 | Total size of the message, **including** these 4 bytes (= body + 14) |
| 4 | u16 | Version, always **1** |
| 6 | u16 | Type (table 3.3) |
| 8 | u16 | Reserved, 0 |
| 10 | u32 | Message **ID**, which matches a request to its response. The client counts 1, 2, 3… per connection; messages without a response use 0 |
| 14 | … | Body |

### 3.2 Framing
- **TCP**: messages are sent back to back. Read the leading u32 (never < 14) and accumulate bytes until
  the message is complete.
- **UDP (discovery)**: each datagram has an 8-byte envelope and then **one** message, up to 2048 bytes:
  u32 counter (grows with each datagram), u16 0, u16 1.

### 3.3 Message types

| Type | Name | Body | Direction | Status |
|---|---|---|---|---|
| 0x0001 / 0x0002 | echo request / response | empty | C→S / S→C | open |
| 0x0003 | settings request | u32 mask, u8 head | C→S | real |
| 0x0004 | settings update | XML + attachments | both | real |
| 0x0005 | discovery request | empty | C→S (multicast) | real |
| 0x0006 | discovery response | XML | S→C | real |
| 0x0007 | settings commit | u32 mode, text blob | C→S | open |
| 0x0008 | time request | u32 s, u16 ms (client clock) | C→S | real |
| 0x0009 | time response | the request echoed + u32 s, u16 ms (sonar clock) | S→C | real |
| 0x0010 / 0x0011 | status request / response | empty / XML | C→S / S→C | open |
| 0x0020 – 0x0022 | trigger control | small integers | C→S | open |
| 0x0030 | ping request | u32 head, u32 flags, u32 parameter | C→S | real |
| 0x0031 | ping response | XML + attachments | S→C | real |

### 3.4 XML messages
```
blob  XML document with a <message> root element
blob  attachment 0
blob  attachment 1
…     (until the end of the message)
```
The XML points to the attachments by **index** (attributes `Attachment`, `I_Attachment`, …). Every XML
message has the `<message>` root, and the content (`<Sonar>`, `<Ping>`, `<DiscoveryResponse>`) goes
inside it.

## 4. Session

### 4.1 Opening
1. TCP connection to `host:1149` (5 s timeout).
2. **Settings request** with mask **0x2F** and head **0xFF** (all heads), ID 1.
3. The sonar answers with a **settings update** that has the whole configuration (§5).
4. **Time request** (ID 2): the answer gives the clock difference (§4.4).

### 4.2 Settings (range, gain, TVG)
To change a setting, the client sends a **settings update** with only that field, ID 0. The sonar does
not answer. Range from 0 to 8 m on head 0:
```xml
<message><Sonar><Head id="0"><Settings WindowStart="0.000000" WindowStop="8.000000" /></Head></Sonar></message>
```

| Setting | Element | Attribute | Status |
|---|---|---|---|
| range | `Head/Settings` | `WindowStart`, `WindowStop` (m) | real |
| gain | `Head/Settings` | `Gain` | real |
| TVG | `Head/Settings` | `TVG` | real |
| sound speed | `Head/Settings` | `SoundSpeed` | open |
| ping interval | `Head/Settings` | `PingInterval` | open |

The driver clamps the range to the head's `MinRange`…`MaxRange`. The configuration XML uses a
lowercase `id` in `Head` and `Transducer`.

### 4.3 Ping on request
**Ping request** with the head, flags **0x21** and parameter 0xFFFFFFFF, and a new ID. The sonar answers
with a **ping response** with the same ID. Flag 0x40 asks for the answer over UDP multicast instead (open).

### 4.4 Clock
The client sends its clock in the time request. The answer echoes it and adds the sonar clock. The
difference (≈1 ms precision) is **subtracted** from the time of every ping. The real M900/2250 had its
clock near 1970 (difference ≈ −1.79·10⁹ s); after the correction its pings carry the PC's UTC time
(`SYNC.md`).

## 5. Configuration XML (settings update)
```xml
<Sonar SerialNumber="…" Type="N" Mode="…">
  <Name>…</Name>
  <Revision Firmware="N" FPGA="N"/>
  <Head id="0" SerialNumber="…" CenterFreq="…" RxSampFreq="…" TxSampFreq="…"
        ElementSpacing="…" MaxRange="…" MinRange="…" Points="…" WindowPoints="…" HeadType="…">
    <Name>…</Name>
    <Settings WindowStart="…" WindowStop="…" Gain="…" TVG="…" SoundSpeed="…" PingInterval="…"/>
    <Transducer id="0" HighFreq="…" LowFreq="…" MountingAngle="…" Orientation="LEFT|RIGHT|LEFTALT|RIGHTALT">
      <MountingOrientation X_Offset="…" Y_Offset="…" Z_Offset="…" X_Rotation="…" Y_Rotation="…" Z_Rotation="…"/>
    </Transducer>
    <Pulse StartFreq="…" StopFreq="…" TBW="…" Samples="N" Attachment="k"/>
    <Normalization Len="N" Attachment="k"/>
  </Head>
</Sonar>
```
The real sonar also sends `network`, `PartNumber`, `Description`, `Location` and a few power and
trigger attributes, which opensonar ignores. The full XML of our sonar is in
`samples/live/sonar_config_20261014.xml` (not in git).

What each attribute means for the image, and its place in the `.son` (`SON_FORMAT.md`):

| XML | `.son` | Needed for the image |
|---|---|---|
| `Sonar@Type`, `SerialNumber`, `Revision@Firmware`, `Name` | sonar record | no |
| `Head@RxSampFreq` | HEAD_SETTINGS item 12 (sampling rate) | yes |
| `Head@TxSampFreq` | item 13 (reference clock) | yes |
| `Head@ElementSpacing` | item 5 | yes |
| `Head@MinRange` / `MaxRange` | items 6 / 7 | yes |
| `Head@CenterFreq`, `HeadType`, `Points`, `WindowPoints` | items 14, 1, 17, 18 | no |
| `Head/Settings` | items 6–9, 15 | no (the ping carries them) |
| `Transducer@HighFreq` / `LowFreq` | transducer record, 1st / 2nd u32 | yes |
| `Transducer@Orientation` (LEFT 1, RIGHT 0, LEFTALT 0x11, RIGHTALT 0x10) | transducer flags | yes |
| `Transducer/MountingOrientation` (m and degrees) | orientation subtype 6 | yes |
| `Pulse` + attachment (N complex values, re and im as f32) | items 32–33 | yes |
| `Normalization` + attachment (M f32) | items 34–35 | yes |

`MountingAngle` is in degrees.

## 6. Ping response
```xml
<Ping Head="0" Number="123" Timestamp="…" SoundSpeed="1500" WindowStart="1" WindowStop="30" Gain="…" TVG="…">
  <Data Samples="N">
    <Transducer ID="0" Attachment="k" ByteOrder="LITTLE"/>                       <!-- real signal -->
    <Transducer ID="1" I_Attachment="k" Q_Attachment="m" ByteOrder="LITTLE"/>    <!-- I/Q -->
    <Transducer ID="2" AttachmentCompressed="k" ByteOrder="LITTLE"/>             <!-- compressed -->
  </Data>
</Ping>
```
- Here the transducer `ID` is **uppercase**.
- `Samples` is the number of samples per channel; an uncompressed attachment has `Samples × 2` bytes.
- Samples are 12-bit, with zero at 0x800.
- `Timestamp` is Unix seconds (double), in the sonar clock; the driver rounds it to ms and corrects it (§4.4).
- The real sonar sends compressed signals (§7) at ~10–19 pings/s.

## 7. Signal compression (5/7-bit deltas)
Input: u16 words. Output: `Samples` u16 samples. State: a mode (none, 5 bits, 7 bits) and a base.

| Word | Action |
|---|---|
| bit 15 = 0, bits 14–12 = 100 | **5-bit** mode; base = (word & 0x1FFF) × 2 |
| bit 15 = 0, bits 14–12 = 110 | **7-bit** mode; base = (word & 0x1FFF) × 2 |
| bit 15 = 0, other values | **literal** sample = word |
| bit 15 = 1, 5-bit mode | 3 samples: base + bits 0–4, base + bits 5–9, base + bits 10–14 |
| bit 15 = 1, 7-bit mode | 2 samples: base + bits 0–6, base + bits 8–14 (bit 7 unused) |
| bit 15 = 1, no mode | error |

Missing samples at the end are filled with 0x800, and extra ones are dropped. The real sonar sends 1–2
extra samples, left over from the last group of 3.

## 8. Discovery
- The client joins 224.192.45.34 on UDP 1149 and sends a **discovery request** on each interface, about
  once per second.
- The sonar answers straight to the sender:
  `<message><DiscoveryResponse Host="ip" Port="1149" UUID="…" MulticastIP="…" MulticastPort="…"/></message>`.
- The same sonar can answer on several interfaces; count it once.

## 9. Still open
- The multicast ping path (§1), and the status, echo and trigger messages.
- The role of the reserved header field.
- Real I/Q signals: none of our recordings has a Q channel.
