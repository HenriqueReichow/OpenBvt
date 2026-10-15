# One ping as the sonar sends it: the raw 12-bit signal of every transducer, plus what is needed to
# turn it into images again with opensonar (the chirp replica and the per-beam gains, too big to repeat
# in every ping, are in SonarInfo with the same config_id).

std_msgs/Header header    # stamp: ping time, UTC, corrected for the sonar clock (docs/SYNC.md); frame_id: the head frame
string serial             # sonar serial number
uint8 head                # head index
uint32 number             # ping number given by the sonar (or by the file)
uint64 seq                # counter of the opensonar service: consecutive values = no ping lost
int32 tz_offset           # s, local time zone offset of the recording PC (as in .son files)

float32 range_start       # m
float32 range_stop        # m
float32 sound_speed       # m/s
float32 gain
float32 tvg
uint32 range_bins         # rows of the R-Theta image for this range (they change with the range)
uint32 beams              # columns of the R-Theta image
float32 range_resolution  # m per range bin
float32 bin_range_near    # m, range of the nearest R-Theta row (bin centre)
float32 bin_range_far     # m, range of the farthest R-Theta row

uint32[] samples          # samples per transducer, in order
uint16[] signal           # I channel of all transducers, concatenated (12 bit, zero at 0x800)
uint16[] signal_q         # Q channel, the same layout; empty when the sonar sends no I/Q

# configuration to reprocess (compact part; the full one is SonarInfo with the same config_id)
string config_id
uint32 sample_rate        # Hz, signal sampling rate
uint32 pulse_sample_rate  # Hz, sampling rate of the chirp replica
float32 element_spacing   # m
uint8 mapping_method      # 0 or 1 (docs/IMAGING.md)
TransducerConfig[] transducers
