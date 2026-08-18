#!/usr/bin/env bash
# Apply explicit SooperLooper input_latency on all loops (P0 calibration).
# Usage: set-input-latency.sh <samples>   e.g. 1024, 2048, 3072
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SAMPLES="${1:?usage: set-input-latency.sh <samples>}"
export MPE_SL_INPUT_LATENCY="$SAMPLES"
export MPE_SL_AUTOSET_LATENCY=0
python3 "${SCRIPT_DIR}/sl_grid_sync.py" >/dev/null
echo "Applied input_latency=${SAMPLES} (autoset off) on all loops."
exec "${SCRIPT_DIR}/dump-loop-levels.py" --detail | head -4
