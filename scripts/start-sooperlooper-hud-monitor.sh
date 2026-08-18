#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "NOTE: HUD merged into mpe-looper-session.service — use looper-session.py --hud-only for debug." >&2
exec env PYTHONUNBUFFERED=1 python3 "${SCRIPT_DIR}/looper-session.py" --hud-only
