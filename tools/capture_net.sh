#!/bin/sh
# Captures the traffic of the sonar's interface into vendor/capture/ while a live session runs.
# Usage: sudo tools/capture_net.sh INTERFACE [SONAR_IP]    (Ctrl+C to stop)
# With SONAR_IP, keeps only the traffic to/from the sonar (plus discovery broadcast/multicast and ARP).
# Uses dumpcap (pcapng directly) if present; otherwise tcpdump (pcap), converted with editcap if available.
set -e
[ -n "$1" ] || { echo "usage: $0 INTERFACE [SONAR_IP]  (interfaces: $(ls /sys/class/net | tr '\n' ' '))" >&2; exit 2; }
[ -e "/sys/class/net/$1" ] || { echo "interface '$1' does not exist" >&2; exit 2; }
ROOT=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$ROOT/vendor/capture"
BASE="$ROOT/vendor/capture/sonar_$(date +%Y%m%d_%H%M%S)"
FILTER=${2:+"host $2 or broadcast or multicast or arp"}

if command -v dumpcap >/dev/null; then
    echo "recording $BASE.pcapng (Ctrl+C to stop)"
    exec dumpcap -i "$1" -w "$BASE.pcapng" ${FILTER:+-f "$FILTER"}
fi
echo "recording $BASE.pcap (Ctrl+C to stop)"
trap : INT
tcpdump -i "$1" -s 0 -U -w "$BASE.pcap" $FILTER || true
if command -v editcap >/dev/null; then
    editcap -F pcapng "$BASE.pcap" "$BASE.pcapng" && rm "$BASE.pcap" && echo "saved $BASE.pcapng"
else
    echo "saved $BASE.pcap (install wireshark-common and run: editcap -F pcapng FILE.pcap FILE.pcapng)"
fi
chown "${SUDO_USER:-$(id -un)}" "$BASE".pcap* 2>/dev/null || true
