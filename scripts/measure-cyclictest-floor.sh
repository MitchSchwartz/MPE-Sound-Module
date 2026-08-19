#!/bin/bash
# Record cyclictest wakeup latency floor — Step 0 of low-latency-512-256-spec.md
#
# Appends results; never truncates the output file. Exits non-zero if cyclictest
# is missing or the run fails.
#
# Usage:
#   ./scripts/measure-cyclictest-floor.sh [--output FILE] [--label TAG]
#
# Default command (from spec):
#   cyclictest -m -t1 -p 80 -n -i 200 -l 300000

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

OUTPUT="${MPE_CYCLICTEST_LOG:-$HOME/latency-cyclictest.log}"
LABEL="stock-kernel"

while [ $# -gt 0 ]; do
    case "$1" in
        --output) OUTPUT="${2:?--output requires a path}"; shift 2 ;;
        --label) LABEL="${2:?--label requires a value}"; shift 2 ;;
        -h | --help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

if ! command -v cyclictest >/dev/null 2>&1; then
    echo "ERROR: cyclictest not found — install rt-tests (sudo apt install rt-tests)" >&2
    exit 1
fi

{
    echo "=== cyclictest floor label=${LABEL} $(date -Is) ==="
    echo "SENTINEL cyclictest-start"
    if [ -d "$MPE_MODULE_REPO/.git" ]; then
        git -C "$MPE_MODULE_REPO" log --oneline -1 2>/dev/null || true
        git -C "$MPE_MODULE_REPO" status --porcelain 2>/dev/null || true
    fi
    uname -r
    tr '\0' ' ' < /proc/cmdline 2>/dev/null || true
    echo
    cyclictest -m -t1 -p 80 -n -i 200 -l 300000
    echo "SENTINEL cyclictest-end"
    echo
} >>"$OUTPUT"

echo "Appended cyclictest floor to $OUTPUT"
echo "SENTINEL cyclictest-logged"
