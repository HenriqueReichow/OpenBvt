# Event mark (opensonar GUI "Mark", the mark service, or `opensonar ctl mark`): UTC time and text.
std_msgs/Header header    # stamp: when the mark was made (PC clock, UTC)
string serial
string text
uint64 seq                # seq of the next ping: places the mark between pings
int64 recorded_pings      # pings already in the recording, −1 if not recording
