#!/bin/sh
# Tests dist/*.deb and dist/*.AppImage in clean Ubuntu 22.04 and 24.04 containers (Docker).
# Usage: packaging/test_packages.sh SAMPLE.son [ubuntu:22.04 ubuntu:24.04]
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SAMPLE=$(realpath "$1"); shift
IMAGES=${*:-"ubuntu:22.04 ubuntu:24.04"}
for img in $IMAGES; do
    echo "################ $img"
    docker run --rm -v "$ROOT/dist:/pkg:ro" -v "$SAMPLE:/data/sample.son:ro" \
        -v "$ROOT/packaging/test_in_container.sh:/test.sh:ro" "$img" sh /test.sh
done
