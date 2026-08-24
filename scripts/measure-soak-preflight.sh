#!/bin/bash
# Fast gate before V12 / long soak — poly state + ~45s DSP parity spot-check (<2 min setup tail).
#
# Usage:
#   sudo ./scripts/measure-soak-preflight.sh [--patch-name "Cloud Horn"] [--voices 5] \
#       [--buffer 512] [--periods 2] [--governor off] [--output FILE]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
exec "$SCRIPT_DIR/measure-soak-instrument.sh" --preflight-only "$@"
