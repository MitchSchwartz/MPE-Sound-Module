#!/bin/bash
# Regression: measure-soak-instrument must not read meter in command substitution
# (subshell drops MPE_METER_LAST_AGE_S → set -u abort at minute 1, occurrence 11).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/scripts/measure-soak-instrument.sh"
V12="$ROOT/scripts/measure-v12-buffer-compare.sh"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

ok() {
    echo "OK: $*"
}

[ -f "$SCRIPT" ] || fail "missing $SCRIPT"
[ -f "$V12" ] || fail "missing $V12"

grep -q '_read_meter_xruns()' "$SCRIPT" || fail "missing _read_meter_xruns helper"

if grep -E '\$\(mpe_meter_xruns_read\)|`mpe_meter_xruns_read`' "$SCRIPT"; then
    fail "command substitution on mpe_meter_xruns_read still present"
fi

if grep -q 'MPE_METER_LAST_AGE_S' "$SCRIPT" && ! grep -q 'METER_AGE_S' "$SCRIPT"; then
    fail "log line references MPE_METER_LAST_AGE_S without local METER_AGE_S capture"
fi

grep -q '\-\-minutes' "$SCRIPT" || fail "missing --minutes flag"
grep -q '\-\-governor' "$SCRIPT" || fail "missing --governor flag"
grep -q '_provenance_line' "$SCRIPT" || fail "missing _provenance_line"
grep -q 'governor_engagements=' "$SCRIPT" || fail "missing governor_engagements per minute"
grep -q 'dsp_median=' "$SCRIPT" || fail "missing dsp_median in RESULT"

grep -q 'measure-soak-instrument.sh' "$V12" || fail "V12 must delegate to soak script"
grep -q 'NOTE: no PASS/FAIL' "$V12" || fail "V12 must not emit PASS/FAIL verdicts"
grep -q 'fano=' "$V12" || fail "V12 summary must compute fano"

ok "measure-soak-instrument subshell guard + V12 flags intact"
echo "test_measure_soak_instrument.sh: all checks passed"
