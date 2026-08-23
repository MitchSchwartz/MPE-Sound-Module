#!/bin/bash
# Regression: measure-soak-instrument must not read meter in command substitution
# (subshell drops MPE_METER_LAST_AGE_S → set -u abort at minute 1, occurrence 11).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/measure-soak-instrument.sh"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

ok() {
    echo "OK: $*"
}

[ -f "$SCRIPT" ] || fail "missing $SCRIPT"

grep -q '_read_meter_xruns()' "$SCRIPT" || fail "missing _read_meter_xruns helper"

if grep -E '\$\(mpe_meter_xruns_read\)|`mpe_meter_xruns_read`' "$SCRIPT"; then
    fail "command substitution on mpe_meter_xruns_read still present"
fi

if grep -q 'MPE_METER_LAST_AGE_S' "$SCRIPT" && ! grep -q 'METER_AGE_S' "$SCRIPT"; then
    fail "log line references MPE_METER_LAST_AGE_S without local METER_AGE_S capture"
fi

ok "measure-soak-instrument subshell guard intact"
echo "test_measure_soak_instrument.sh: all checks passed"
