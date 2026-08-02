#!/bin/bash
# Measure shutdown timing from the previous boot (journal deltas + app trace).
# Usage: ./scripts/shutdown-measure-last.sh [--boot -1]
#
# Before a deliberate test run:
#   ./scripts/shutdown-mark-test.sh ui "power menu confirm"
#   # then shut down from the touch UI
# After boot:
#   ./scripts/shutdown-measure-last.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
exec python3 "$SCRIPT_DIR/shutdown_measure.py" "$@"
