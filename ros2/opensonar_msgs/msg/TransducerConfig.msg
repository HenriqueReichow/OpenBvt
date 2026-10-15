# One transducer (sector) of a sonar head, as the image generator needs it.
uint16 index
uint32 high_freq          # Hz, upper frequency of the chirp sector
uint32 low_freq           # Hz, lower frequency
uint16 flags              # orientation: 0 RIGHT, 1 LEFT, 0x10 RIGHTALT, 0x11 LEFTALT
float32[3] position       # m, in the head frame
float32[3] rotation       # degrees (roll, pitch, yaw) in the head frame
bool oriented             # true if the recording has a per-transducer orientation (image method 1)
