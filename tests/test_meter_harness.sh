#!/bin/bash
# T6/I2 — meter.state harness must fail loudly when blind (never || echo 0).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../scripts/lib/paths.sh
source "$ROOT/scripts/lib/paths.sh"
# shellcheck source=../scripts/lib/audio-engine.sh
source "$ROOT/scripts/lib/audio-engine.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fresh_meter() {
    local f="$TMP/meter.state"
    printf 'xruns=5\nupdated=%s\npeak_linear=0\nwired=1\n' "$EPOCHSECONDS" >"$f"
    printf '%s\n' "$f"
}

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

ok() {
    echo "OK: $*"
}

MPE_METER_HARNESS_MAX_AGE_S=3
export MPE_METER_STATE_FILE

# Fresh meter passes
f="$(fresh_meter)"
MPE_METER_STATE_FILE="$f"
xr="$(mpe_meter_xruns_read)" || fail "fresh meter should pass"
[ "$xr" = "5" ] || fail "expected xruns=5 got $xr"
ok "fresh meter xruns=$xr age=${MPE_METER_LAST_AGE_S:-0}s"

# Missing file fails
MPE_METER_STATE_FILE="$TMP/missing.state"
if mpe_meter_xruns_read 2>/dev/null; then
    fail "missing meter should fail"
fi
ok "missing meter fails"

# Stale meter fails
f="$(fresh_meter)"
printf 'xruns=0\nupdated=%s\n' "$((EPOCHSECONDS - 120))" >"$f"
MPE_METER_STATE_FILE="$f"
if mpe_meter_xruns_read 2>/dev/null; then
    fail "stale meter should fail"
fi
ok "stale meter fails"

# Empty xruns key fails
f="$TMP/bad.state"
printf 'updated=%s\n' "$EPOCHSECONDS" >"$f"
MPE_METER_STATE_FILE="$f"
if mpe_meter_xruns_read 2>/dev/null; then
    fail "missing xruns= should fail"
fi
ok "missing xruns= fails"

echo "test_meter_harness.sh: all checks passed"
