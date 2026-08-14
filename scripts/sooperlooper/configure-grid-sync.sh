#!/usr/bin/env bash
# Apply SooperLooper sync/quantize after engine start (grid default, free-form optional).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "${SCRIPT_DIR}/sl_grid_sync.py"
