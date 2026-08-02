#!/usr/bin/env bash
# Print stop timeline from the previous boot (journal + shutdown splash log).
# Prefer shutdown-measure-last.sh for computed deltas and trace correlation.
# Usage: ./scripts/shutdown-analyze-last.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
exec "$SCRIPT_DIR/shutdown-measure-last.sh" "$@"
