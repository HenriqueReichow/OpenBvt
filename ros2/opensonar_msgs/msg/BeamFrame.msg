# One frame of a sonar that forms its beams in hardware (.aris family): intensities on a
# range × beam grid, ready for 3D reconstruction. Raw-signal sonars publish RawPing instead.

std_msgs/Header header    # stamp: frame time, UTC; frame_id: the head frame
string serial
uint8 head
uint32 number             # frame number given by the sonar (or by the file)
uint64 seq                # counter of the opensonar service: consecutive values = no frame lost

float32 sound_speed       # m/s
float32 gain              # dB; 0 when unknown
uint32 samples            # rows: range samples
uint32 beams              # columns
float32 range_start       # m, range of row 0 (the nearest)
float32 range_resolution  # m between rows
float32[] beam_angles     # rad, one per column; ROS convention (REP-103): positive to the LEFT
float32 vertical_fov      # rad, elevation aperture of the beams; 0 when unknown
uint8[] intensity         # samples × beams, row-major, row 0 = nearest range (0–255)
