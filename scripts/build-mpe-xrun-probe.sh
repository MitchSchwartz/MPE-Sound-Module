#!/usr/bin/env bash
# Build native/mpe-xrun-probe when libjack-dev is present.
set -euo pipefail

REQUIRED=0
if [ "${1:-}" = "--required" ]; then
    REQUIRED=1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/native/mpe-xrun-probe"

if ! pkg-config --exists jack 2>/dev/null; then
    if [ "$REQUIRED" -eq 1 ]; then
        echo "ERROR: libjack-dev not installed" >&2
        exit 1
    fi
    echo "SKIP: libjack-dev not installed — mpe-xrun-probe not built" >&2
    exit 0
fi

make -C "$DIR" clean all
echo "Built $DIR/mpe-xrun-probe"
