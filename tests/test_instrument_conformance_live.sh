#!/bin/bash
# C0 — live instrument conformance (meter path + Pi-only positive controls).
#
# On nerdrack: negative controls against synthetic meter.state always run.
# Positive controls (forced xrun delta, DSP band) require a live appliance meter.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../scripts/lib/paths.sh
source "${ROOT}/scripts/lib/paths.sh"
# shellcheck source=../scripts/lib/mpe-services.sh
source "${ROOT}/scripts/lib/mpe-services.sh"
# shellcheck source=../scripts/lib/audio-engine.sh
source "${ROOT}/scripts/lib/audio-engine.sh"

fail() { echo "LIVE FAIL: $*" >&2; exit 1; }
ok() { echo "LIVE OK: $*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

MPE_METER_HARNESS_MAX_AGE_S=3
export MPE_METER_STATE_FILE

fresh_meter() {
    printf 'xruns=5\nupdated=%s\npeak_linear=0\nwired=1\n' "$EPOCHSECONDS" >"$1"
}

# --- 2b negative: missing meter halts ---
MPE_METER_STATE_FILE="$TMP/missing.state"
if mpe_meter_xruns_read 2>/dev/null; then
    fail "missing meter should halt"
fi
ok "negative: missing meter.state halts"

# --- 2b negative: stale meter halts ---
f="$TMP/stale.state"
fresh_meter "$f"
printf 'xruns=0\nupdated=%s\n' "$((EPOCHSECONDS - 120))" >"$f"
MPE_METER_STATE_FILE="$f"
if mpe_meter_xruns_read 2>/dev/null; then
    fail "stale meter should halt"
fi
ok "negative: stale meter.state halts"

# --- 2b negative: empty xruns key halts ---
printf 'updated=%s\npeak_linear=0\n' "$EPOCHSECONDS" >"$TMP/bad.state"
MPE_METER_STATE_FILE="$TMP/bad.state"
if mpe_meter_xruns_read 2>/dev/null; then
    fail "missing xruns= should halt"
fi
ok "negative: missing xruns= halts"

# --- 2b negative: simulate stopped meter mid-read (file removed) ---
f="$(fresh_meter "$TMP/live.state"; echo "$TMP/live.state")"
MPE_METER_STATE_FILE="$f"
mpe_meter_xruns_read >/dev/null || fail "fresh meter should read"
rm -f "$f"
if mpe_meter_xruns_read 2>/dev/null; then
    fail "removed meter mid-cell should halt"
fi
ok "negative: meter removed mid-cell halts"

# --- 2a positive: live appliance meter (Pi only) ---
REAL_METER="/run/mpe/meter.state"
MPE_METER_STATE_FILE="$REAL_METER"
if [ ! -r "$REAL_METER" ] || ! mpe_meter_assert_live 2>/dev/null; then
    echo "LIVE SKIP: no live meter at ${REAL_METER} — Pi positive controls run via measure-instrument-conformance-live.sh" >&2
    echo "test_instrument_conformance_live.sh: negative controls passed (Pi deferred)"
    exit 0
fi

MPE_METER_STATE_FILE="$REAL_METER"
start="$(mpe_meter_xruns_read)" || fail "live meter read start"
sleep 2
end="$(mpe_meter_xruns_read)" || fail "live meter read end"
ok "positive: live meter readable (start=${start} end=${end})"

if [ "$(mpe_read_appliance_env_var MPE_PEAK_METER 2>/dev/null || echo 0)" != "1" ]; then
    fail "MPE_PEAK_METER must be 1 for live conformance"
fi
ok "positive: MPE_PEAK_METER=1"

echo "test_instrument_conformance_live.sh: negative controls passed; live meter present"
echo "NOTE: full 2a load/xrun/DSP band checks run via measure-instrument-conformance-live.sh on Pi"
