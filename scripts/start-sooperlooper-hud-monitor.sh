#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec env PYTHONUNBUFFERED=1 python3 "${SCRIPT_DIR}/sl-hud-monitor.py"
