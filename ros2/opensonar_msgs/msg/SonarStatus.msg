# State of the sonar node and of the opensonar service behind it (1 Hz).
std_msgs/Header header
bool connected
bool live                 # false: playing a .son file
string source             # IP or file
string serial
bool running              # pinging
uint8[] heads             # heads being pinged
uint64 published          # pings published by the service
uint64 received           # pings received by this node
uint64 lost               # gaps in the service's seq counter seen by this node
string recording          # .son being recorded, empty if none
uint64 recorded           # pings in the recording
float64 clock_offset      # s, sonar clock − PC clock (already removed from every stamp)
string error              # last error of the service, empty if none
