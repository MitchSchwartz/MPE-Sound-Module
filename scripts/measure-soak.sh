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
# 15, not 16. Index 15 is the phantom sl_limits.MAX_USABLE_LOOPS exists to
# keep out: it answers reads with plausible defaults and discards writes, so
# a soak at 16 measures fourteen real loops and one that only looks real.
# This value is sed'd into the persistent /etc/mpe/mpe.env below and is NOT
# restored afterwards, so it outlives the run and the next deploy.
LOOPS=15
# shellcheck source=lib/measure-env.sh
source "$SCRIPT_DIR/lib/measure-env.sh"
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

RESTORE_BUFFER=""
_cleanup() {
    [ -n "$MIDI_PID" ] && kill "$MIDI_PID" 2>/dev/null || true
    if [ -n "$RESTORE_BUFFER" ]; then
        "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$RESTORE_BUFFER" \
            || echo "ERROR: could not restore buffer ${RESTORE_BUFFER}" >&2
    fi
}
# _cleanup first, then mpe.env comes back byte for byte (lib/measure-env.sh).
mpe_env_arm _cleanup

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

_live_buffer="$(mpe_env_get MPE_JACK_BUFFER)"
if [ "$_live_buffer" != "$BUFFER" ]; then
    # set-surge-audio.sh edits the env file itself: snapshot it first.
    mpe_env_snapshot
    RESTORE_BUFFER="${_live_buffer:-256}"
    if ! "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$BUFFER"; then
        echo "ERROR: set-surge-audio --buffer ${BUFFER} failed" >&2
        exit 1
    fi
    sleep 8
fi

# Condition D — full stack
systemctl start mpe-sooperlooper.service
sleep 6
systemctl start mpe-looper-session.service
sleep 6
systemctl start sl-watchdog.service
sleep 6
systemctl start mpe-peak-meter.service 2>/dev/null || true
sleep 2

mpe_env_set MPE_SL_LOOPS "$LOOPS"
if [ "$MPE_ENV_LAST_CHANGED" = 1 ]; then
    mpe_env_on_restore systemctl restart mpe-sooperlooper.service
    systemctl restart mpe-sooperlooper.service
    sleep 8
fi

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
