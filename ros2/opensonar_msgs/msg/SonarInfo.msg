# Description of one sonar head. Published with transient_local QoS when the node connects and
# whenever the range, gain or TVG change, so a late subscriber (or a rosbag) always gets it.

std_msgs/Header header    # stamp: when it was published; frame_id: the head frame
string serial
string sonar_model        # e.g. M900/2250-130
string firmware
uint8 head
string head_model         # e.g. M900-130
uint32 center_freq        # Hz

uint32 beams              # columns of the R-Theta image
float32[] beam_angles     # rad, one per beam; ROS convention (REP-103): positive to the LEFT (counter-clockwise, z up)
float32 field_of_view     # rad, from the first to the last beam

float32 range_start       # m, current window
float32 range_stop        # m
uint32 range_bins         # rows of the R-Theta image at the current range
float32 range_resolution  # m per bin at the current range
float32 bin_range_near    # m, range of the nearest R-Theta row (bin centre)
float32 bin_range_far     # m, range of the farthest R-Theta row
float32 range_min_limit   # m, what the head allows
float32 range_max_limit   # m
float32 sound_speed       # m/s
float32 gain
float32 tvg
float32 ping_rate         # Hz, measured by the node for this head

# full configuration to reprocess the raw pings (opensonar.son.config_from_dict)
string config_id
uint32 sample_rate
uint32 pulse_sample_rate
float32 element_spacing
uint8 mapping_method
TransducerConfig[] transducers
float32[] pulse_real      # chirp replica (real part)
float32[] pulse_imag      # chirp replica (imaginary part)
float32[] normalization   # per-beam gain table
