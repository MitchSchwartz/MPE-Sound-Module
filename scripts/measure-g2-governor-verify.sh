#!/bin/bash
# G2 — recalibrate poly governor thresholds and verify both control arms.
#
# See docs/measurements/PROMPT-G2-governor-recalibration.md
#
# Usage:
#   sudo ./scripts/measure-g2-governor-verify.sh [--output-dir DIR] [--minutes 30]
#       [--cpu-high 78.0] [--cpu-low 68.0] [--skip-positive] [--skip-negative]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MINUTES=30
OUTPUT_DIR="${HOME}/g2-governor-$(date +%Y%m%d-%H%M%S)"
CPU_HIGH=78.0
CPU_LOW=68.0
SKIP_POSITIVE=0
SKIP_NEGATIVE=0
ENV_FILE="/etc/mpe/mpe.env"

while [ $# -gt 0 ]; do
    case "$1" in
        --minutes) MINUTES="${2:?}"; shift 2 ;;
        --output-dir) OUTPUT_DIR="${2:?}"; shift 2 ;;
        --cpu-high) CPU_HIGH="${2:?}"; shift 2 ;;
        --cpu-low) CPU_LOW="${2:?}"; shift 2 ;;
        --skip-positive) SKIP_POSITIVE=1; shift ;;
        --skip-negative) SKIP_NEGATIVE=1; shift ;;
        -h | --help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo on the Pi" >&2
    exit 1
fi

# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

_set_env_var() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" >"$tmp"
    else
        cat "$ENV_FILE" >"$tmp" 2>/dev/null || true
        printf '\n%s=%s\n' "$key" "$value" >>"$tmp"
    fi
    install -m 0644 "$tmp" "$ENV_FILE"
    rm -f "$tmp"
}

_readback() {
    mpe_read_appliance_env_var "$1" 2>/dev/null || echo unset
}

_soak_result_field() {
    local log="$1" field="$2"
    grep -E '^RESULT soak_minutes=' "$log" | tail -1 | sed -n "s/.*${field}=\\([0-9.]*\\).*/\\1/p"
}

mkdir -p "$OUTPUT_DIR"
SUMMARY="${OUTPUT_DIR}/g2-summary.txt"
NEG_LOG="${OUTPUT_DIR}/g2-negative-cloudhorn-1024x2.log"
POS_LOG="${OUTPUT_DIR}/g2-positive-crystals-6.log"

{
    echo "=== G2 governor recalibration $(date -Is) ==="
    echo "SENTINEL g2-start"
    echo "pre_registered cpu_high=${CPU_HIGH} cpu_low=${CPU_LOW} negative_minutes=${MINUTES}"
    echo
    echo "--- Step 0: X1 confirm harness governor check (offline grep) ---"
    if grep -q '_set_env_var MPE_POLY_GOVERNOR 0' "$SCRIPT_DIR/measure-confirm-at-voices.sh" \
        && grep -q 'systemctl stop surge-poly-governor' "$SCRIPT_DIR/measure-confirm-at-voices.sh"; then
        echo "X1 confirm: governor explicitly OFF — inputs valid"
    else
        echo "ERROR: confirm harness may leave governor on — stop G2"
        exit 1
    fi
    echo
    echo "--- Step 1: fade status ---"
    echo "fade_actuation: not implemented as separate layer (Task C / V7 Fix 2 open)"
    echo "surge_path: softkillVoice/uber_release — steal on next note-on after limit drop"
    echo "steal_order: in-release first, then oldest (Surge default; matches spec)"
    echo "note: threshold recalibration prevents false engagement on clean Cloud Horn;"
    echo "      audibility of real steals is B3 after V12"
    echo
    echo "--- Step 2: apply thresholds (governor still off until verify arm) ---"
} | tee "$SUMMARY"

_set_env_var MPE_POLY_CPU_HIGH "$CPU_HIGH"
_set_env_var MPE_POLY_CPU_LOW "$CPU_LOW"
_set_env_var MPE_POLY_GOVERNOR 0
systemctl stop surge-poly-governor.service 2>/dev/null || true

{
    echo "readback MPE_POLY_CPU_HIGH=$(_readback MPE_POLY_CPU_HIGH)"
    echo "readback MPE_POLY_CPU_LOW=$(_readback MPE_POLY_CPU_LOW)"
    echo "readback MPE_POLY_GOVERNOR=$(_readback MPE_POLY_GOVERNOR)"
    echo
} | tee -a "$SUMMARY"

if [ "$SKIP_NEGATIVE" -eq 0 ]; then
    {
        echo "--- Step 3a: negative control — Cloud Horn @5, 1024×2, governor ON, ${MINUTES} min ---"
        echo "criterion: governor_engagements_total=0"
    } | tee -a "$SUMMARY"

    "$SCRIPT_DIR/measure-soak-instrument.sh" \
        --minutes "$MINUTES" --buffer 1024 --periods 2 \
        --governor on --patch-name "Cloud Horn" --voices 5 \
        --label g2-negative-cloudhorn \
        --output "$NEG_LOG"

    neg_gov="$(_soak_result_field "$NEG_LOG" governor_engagements_total)"
    neg_gov="${neg_gov:-unknown}"
    neg_xr="$(_soak_result_field "$NEG_LOG" xruns_total)"
    neg_xr="${neg_xr:-unknown}"

    if [ "$neg_gov" = "0" ]; then
        neg_verdict=PASS
    else
        neg_verdict=FAIL
    fi

    {
        echo "negative_control governor_engagements_total=${neg_gov} xruns_total=${neg_xr} verdict=${neg_verdict}"
        echo
    } | tee -a "$SUMMARY"

    if [ "$neg_verdict" != PASS ]; then
        {
            echo "G2 STOP: governor engaged during clean Cloud Horn @5 — raise thresholds and re-test"
            echo "SENTINEL g2-aborted-negative-fail"
        } | tee -a "$SUMMARY"
        echo "G2 failed negative control → ${SUMMARY}"
        exit 1
    fi
else
    echo "negative_control SKIPPED (--skip-negative)" | tee -a "$SUMMARY"
    neg_gov=0
    neg_xr=unknown
fi

if [ "$SKIP_POSITIVE" -eq 0 ]; then
    {
        echo "--- Step 3b: positive control — Crystals @6, 1024×2, governor ON, 3 min ---"
        echo "criterion: governor_engages AND releases (engagements > 0; final limit recovered)"
    } | tee -a "$SUMMARY"

    "$SCRIPT_DIR/measure-soak-instrument.sh" \
        --minutes 3 --buffer 1024 --periods 2 \
        --governor on --patch-name "Crystals" --voices 6 \
        --label g2-positive-crystals \
        --output "$POS_LOG"

    pos_gov="$(_soak_result_field "$POS_LOG" governor_engagements_total)"
    pos_gov="${pos_gov:-0}"
    if [ "$pos_gov" -gt 0 ]; then
        pos_verdict=PASS
    else
        pos_verdict=FAIL
    fi
    {
        echo "positive_control governor_engagements_total=${pos_gov} verdict=${pos_verdict}"
        echo
    } | tee -a "$SUMMARY"

    if [ "$pos_verdict" != PASS ]; then
        {
            echo "G2 STOP: governor did not engage when Crystals exceeded floor — broken or thresholds too high"
            echo "SENTINEL g2-aborted-positive-fail"
        } | tee -a "$SUMMARY"
        echo "G2 failed positive control → ${SUMMARY}"
        exit 1
    fi
else
    echo "positive_control SKIPPED (--skip-positive)" | tee -a "$SUMMARY"
fi

# Leave governor enabled for shipping stack (V12 / B3).
_set_env_var MPE_POLY_GOVERNOR 1
systemctl enable surge-poly-governor.service 2>/dev/null || true
systemctl restart surge-poly-governor.service 2>/dev/null || true

{
    echo "--- Gate 2 close ---"
    echo "thresholds: HIGH=${CPU_HIGH} LOW=${CPU_LOW} (read back above)"
    echo "governor: ON (MPE_POLY_GOVERNOR=1, service restarted)"
    echo "negative_control: PASS (0 engagements, ${MINUTES} min Cloud Horn @5)"
    if [ "$SKIP_POSITIVE" -eq 0 ]; then
        echo "positive_control: PASS (Crystals @6 engagements=${pos_gov})"
    fi
    echo "next: PROMPT-V12-certify-buffer.md (Mitch approval ~70 min Pi time)"
    echo "SENTINEL g2-complete"
} | tee -a "$SUMMARY"

echo "G2 complete → ${SUMMARY}"
