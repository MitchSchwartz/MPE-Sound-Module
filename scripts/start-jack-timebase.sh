#!/usr/bin/env bash
# Start JACK timebase master for looper grid (background).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONUNBUFFERED=1 python3 "${SCRIPT_DIR}/sooperlooper/jack_timebase.py" "$@"
