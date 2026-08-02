#!/bin/bash
# Mark the start of a deliberate shutdown test (persists across reboot).
#
# Usage:
#   ./scripts/shutdown-mark-test.sh ui "optional note"
#   ./scripts/shutdown-mark-test.sh ssh "systemctl poweroff from laptop"
#   ./scripts/shutdown-mark-test.sh cal "shutdown during calibration loader"
#
# Then perform the shutdown. After the Pi boots, run:
#   ./scripts/shutdown-measure-last.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

METHOD="${1:-manual}"
NOTE="${2:-}"
LOG_DIR="$MPE_MODULE_REPO/logs"
MARKER="$LOG_DIR/shutdown-test-marker.json"

mkdir -p "$LOG_DIR"

python3 - "$METHOD" "$NOTE" "$MARKER" <<'PY'
import json
import socket
import sys
import time
from pathlib import Path

method, note, marker = sys.argv[1:4]
payload = {
    "method": method,
    "note": note,
    "marked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "marked_epoch": round(time.time(), 3),
    "hostname": socket.gethostname(),
}
Path(marker).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print("Marked shutdown test:")
print(json.dumps(payload, indent=2))
print()
print("Next: shut down (UI or SSH), boot, then run:")
print(f"  {Path(marker).parent.parent / 'scripts' / 'shutdown-measure-last.sh'}")
PY
