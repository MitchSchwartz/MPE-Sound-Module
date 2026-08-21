#!/bin/bash
# T5 — long soak: shipping config certification.
#
# Default: 1024×3, condition D (full stack), 16 loops recorded and playing, 8 h.
# Per-minute samples with meter liveness; per-hour xrun breakdown.
#
# Usage:
#   sudo ./scripts/measure-soak.sh [--hours 8] [--output FILE] [--buffer 1024]
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
BUFFER=1024
LOOPS=16
ENV_FILE="/etc/mpe/mpe.env"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
MIDI_PID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --hours) HOURS="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        --buffer) BUFFER="${2:?}"; shift 2 ;;
        --loops) LOOPS="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,14p' "$0"; exit 0 ;;
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

_as_user() {
    if id "$RUN_AS_USER" >/dev/null 2>&1; then
        sudo -u "$RUN_AS_USER" -- "$@"
    else
        "$@"
    fi
}

_meter_field() {
    local key="$1" file
    file="$(mpe_meter_state_file)"
    grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d= -f2-
}

_read_xruns_or_blind() {
    METER_LIVE=0
    if mpe_meter_xruns_read; then
        METER_LIVE=1
        REPLY="$MPE_METER_LAST_XRUNS"
        return 0
    fi
    REPLY="BLIND"
    return 0
}

_cleanup() {
    [ -n "$MIDI_PID" ] && kill "$MIDI_PID" 2>/dev/null || true
}
trap _cleanup EXIT INT TERM

TOTAL_MIN=$((HOURS * 60))
FINISH_EPOCH=$(( $(date +%s) + TOTAL_MIN * 60 ))
FINISH_ISO="$(date -d "@${FINISH_EPOCH}" -Is 2>/dev/null || date -r "$FINISH_EPOCH" -Is 2>/dev/null || echo "in ${HOURS}h")"

echo "T5 soak: ${HOURS}h @ ${BUFFER}×3, condition D, ${LOOPS} loops playing"
echo "Started: $(date -Is)"
echo "Expected finish: ${FINISH_ISO}"
echo "Log: ${OUTPUT}"

{
    echo
    echo "=== measure-soak buffer=${BUFFER} loops=${LOOPS} hours=${HOURS} $(date -Is) ==="
    echo "SENTINEL soak-start"
    echo "expected_finish=${FINISH_ISO}"
} >>"$OUTPUT"

if ! "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$BUFFER"; then
    echo "ERROR: set-surge-audio --buffer ${BUFFER} failed" >&2
    exit 1
fi
sleep 8

# Condition D — full stack
systemctl start mpe-sooperlooper.service
sleep 6
systemctl start mpe-looper-session.service
sleep 6
systemctl start sl-watchdog.service
sleep 6
systemctl start mpe-peak-meter.service 2>/dev/null || true
sleep 2

if ! grep -q "^MPE_SL_LOOPS=" "$ENV_FILE" 2>/dev/null; then
    printf '\nMPE_SL_LOOPS=%s\n' "$LOOPS" >>"$ENV_FILE"
else
    sed -i "s/^MPE_SL_LOOPS=.*/MPE_SL_LOOPS=${LOOPS}/" "$ENV_FILE"
fi
systemctl restart mpe-sooperlooper.service
sleep 8

if ! bash "${SCRIPT_DIR}/sooperlooper/load-n-loops.sh" "$LOOPS"; then
    echo "ERROR: load-n-loops ${LOOPS} failed" >&2
    exit 1
fi

playback="$(_meter_field looper_playback)"
if [ "$playback" != "1" ]; then
    echo "ERROR: looper_playback=${playback:-missing} — loops not playing (verify meter.state)" >&2
    exit 1
fi
echo "verified: looper_playback=1 (${LOOPS} loops playing)" | tee -a "$OUTPUT"

SOAK_SEC=$((HOURS * 3600 + 120))
_as_user python3 "$SCRIPT_DIR/midi-load.py" "$SOAK_SEC" \
    >"/tmp/soak-midi-load.log" 2>&1 &
MIDI_PID=$!
sleep 5

METER_LIVE=0
_read_xruns_or_blind
START_XR="$REPLY"
if [ "$METER_LIVE" != 1 ]; then
    echo "ERROR: meter blind at soak start" >&2
    exit 1
fi
HOUR_START="$START_XR"
hour=1
minute=0
invalid_windows=0
prev_xr="$START_XR"

while [ "$minute" -lt "$TOTAL_MIN" ]; do
    sleep 60
    minute=$((minute + 1))
    _read_xruns_or_blind
    cur="$REPLY"
    temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"
    throttle="$(vcgencmd get_throttled 2>/dev/null || echo 'throttled=unknown')"

    if [ "$METER_LIVE" != 1 ]; then
        invalid_windows=$((invalid_windows + 1))
        {
            echo "SOAK minute=${minute} meter_live=0 INVALID_WINDOW ${temp} ${throttle}"
        } >>"$OUTPUT"
        echo "WARN minute ${minute}: meter blind (window invalid)"
        continue
    fi

    delta=$((cur - prev_xr))
    prev_xr="$cur"
    {
        echo "SOAK minute=${minute} xruns_minute=${delta} xruns_total=$((cur - START_XR)) meter_live=1 meter_age_s=${MPE_METER_LAST_AGE_S} ${temp} ${throttle}"
    } >>"$OUTPUT"

    if [ $((minute % 60)) -eq 0 ]; then
        hour_delta=$((cur - HOUR_START))
        {
            echo "SOAK hour=${hour} minute=${minute} xruns_hour=${hour_delta} xruns_total=$((cur - START_XR)) meter_live=1 invalid_windows=${invalid_windows} ${temp} ${throttle}"
        } >>"$OUTPUT"
        echo "SOAK hour ${hour}: +${hour_delta} xruns (total $((cur - START_XR))) invalid_windows=${invalid_windows}"
        hour=$((hour + 1))
        HOUR_START="$cur"
    fi
done

FINAL=$((prev_xr - START_XR))
{
    echo "RESULT soak_hours=${HOURS} buffer=${BUFFER} loops=${LOOPS} xruns_total=${FINAL} invalid_windows=${invalid_windows}"
    echo "SENTINEL soak-complete"
} >>"$OUTPUT"
echo "Soak complete: ${FINAL} xruns in ${HOURS} h, ${invalid_windows} invalid windows → ${OUTPUT}"
