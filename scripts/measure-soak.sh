#!/bin/bash
# E4 / T5 — long soak: condition A xrun count over many hours.
#
# Reads meter.state every 60 s with liveness checks. Logs temp + throttled each hour.
# Fails loudly if meter goes blind — never reports 0 when uninstrumented.
#
# Usage:
#   sudo ./scripts/measure-soak.sh [--hours 8] [--output FILE]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

HOURS=8
OUTPUT="${MPE_SOAK_LOG:-$HOME/latency-soak.log}"
BUFFER=512

while [ $# -gt 0 ]; do
    case "$1" in
        --hours) HOURS="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        --buffer) BUFFER="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

if [ "$(mpe_read_appliance_env_var MPE_PEAK_METER 2>/dev/null || echo 0)" != "1" ]; then
    echo "ERROR: MPE_PEAK_METER is not 1" >&2
    exit 1
fi

TOTAL_MIN=$((HOURS * 60))

{
    echo
    echo "=== measure-soak buffer=${BUFFER} hours=${HOURS} $(date -Is) ==="
    echo "SENTINEL soak-start"
} >>"$OUTPUT"

# Condition A — synth only (same as measure-latency-run condition A).
systemctl stop mpe-looper-session.service sl-watchdog.service mpe-sooperlooper.service 2>/dev/null || true

START_XR="$(mpe_meter_xruns_read)" || exit 1
HOUR_XR="$START_XR"
HOUR_START="$START_XR"
hour=1
minute=0

while [ "$minute" -lt "$TOTAL_MIN" ]; do
    sleep 60
    minute=$((minute + 1))
    CUR="$(mpe_meter_xruns_read)" || exit 1
    temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"
    throttle="$(vcgencmd get_throttled 2>/dev/null || echo 'throttled=unknown')"

    if [ $((minute % 60)) -eq 0 ]; then
        delta=$((CUR - HOUR_START))
        {
            echo "SOAK hour=${hour} minute=${minute} xruns_hour=${delta} xruns_total=$((CUR - START_XR)) meter_live=1 meter_age_s=${MPE_METER_LAST_AGE_S} ${temp} ${throttle}"
        } >>"$OUTPUT"
        echo "SOAK hour ${hour}: +${delta} xruns (total $((CUR - START_XR)))"
        hour=$((hour + 1))
        HOUR_START="$CUR"
    fi
done

FINAL=$((CUR - START_XR))
{
    echo "RESULT soak_hours=${HOURS} xruns_total=${FINAL} meter_live=1"
    echo "SENTINEL soak-complete"
} >>"$OUTPUT"
echo "Soak complete: ${FINAL} xruns in ${HOURS} h → ${OUTPUT}"
