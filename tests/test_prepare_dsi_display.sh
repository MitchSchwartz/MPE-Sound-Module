#!/bin/bash
# Shell tests for prepare-dsi-display.sh (dev/windowed no-op paths).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREPARE="$ROOT/scripts/prepare-dsi-display.sh"

failures=0

assert_exit() {
    local expected=$1
    shift
    set +e
    "$@" >/dev/null 2>&1
    local rc=$?
    set -e
    if [ "$rc" -ne "$expected" ]; then
        echo "FAIL: expected exit $expected from $* (got $rc)"
        failures=$((failures + 1))
    else
        echo "OK: $* -> exit $rc"
    fi
}

assert_output_contains() {
    local needle=$1
    shift
    if "$@" 2>&1 | grep -q "$needle"; then
        echo "OK: output contains '$needle'"
    else
        echo "FAIL: expected output to contain '$needle' from $*"
        failures=$((failures + 1))
    fi
}

[ -x "$PREPARE" ] || chmod +x "$PREPARE"

assert_exit 0 env MPE_TOUCH_WINDOWED=1 bash "$PREPARE"
assert_output_contains "windowed/dev mode" env MPE_TOUCH_WINDOWED=1 bash "$PREPARE"

assert_exit 0 env DISPLAY=:0 bash "$PREPARE"
assert_output_contains "windowed/dev mode" env DISPLAY=:0 bash "$PREPARE"

if [ "$failures" -gt 0 ]; then
    echo "$failures test(s) failed"
    exit 1
fi

echo "All prepare-dsi-display shell tests passed"
