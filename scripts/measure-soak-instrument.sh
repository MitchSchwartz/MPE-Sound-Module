#!/bin/bash
# Instrument-only overnight soak — 1024×2 tail certification (Gate 1).
#
# Default: Cloud Horn @ 5 voices (V9 verified-clean), condition A, 8 h.
#
# Usage:
#   sudo ./scripts/measure-soak-instrument.sh [--hours 8] [--output FILE] \
#       [--patch-name "Cloud Horn"] [--voices 5]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

HOURS=8
OUTPUT="${MPE_SOAK_LOG:-$HOME/instrument-soak-1024x2.log}"
PATCH_NAME="Cloud Horn"
VOICES=5
BUFFER=1024
PERIODS=2
ENV_FILE="/etc/mpe/mpe.env"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
LOAD_PID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --hours) HOURS="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        --patch-name) PATCH_NAME="${2:?}"; shift 2 ;;
        --voices) VOICES="${2:?}"; shift 2 ;;
        --buffer) BUFFER="${2:?}"; shift 2 ;;
        --periods) PERIODS="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

PATCH_PATH="${QUICK_SELECT}/${PATCH_NAME}.fxp"
[ -f "$PATCH_PATH" ] || { echo "ERROR: missing $PATCH_PATH" >&2; exit 1; }

_as_user() { sudo -u "$RUN_AS_USER" -- "$@"; }

# Command substitution runs mpe_meter_xruns_read in a subshell, so MPE_METER_LAST_AGE_S
# is lost before the log line — set -u then aborts (occurrence eleven, 2026-08-23).
_read_meter_xruns() {
    if mpe_meter_xruns_read >/dev/null; then
        REPLY="$MPE_METER_LAST_XRUNS"
        METER_AGE_S="$MPE_METER_LAST_AGE_S"
        return 0
    fi
    return 1
}

STAGE=init
SOAK_COMPLETE=0

_cleanup() {
    local rc=$?
    [ -n "${LOAD_PID:-}" ] && kill "$LOAD_PID" 2>/dev/null || true
    if [ "${SOAK_COMPLETE:-0}" -eq 0 ] && [ -n "${OUTPUT:-}" ]; then
        echo "SENTINEL soak-aborted stage=${STAGE:-unknown} rc=${rc}" >>"$OUTPUT" 2>/dev/null || true
    fi
}
trap _cleanup EXIT INT TERM

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

TOTAL_MIN=$((HOURS * 60))
FINISH_EPOCH=$(( $(date +%s) + TOTAL_MIN * 60 ))
FINISH_ISO="$(date -d "@${FINISH_EPOCH}" -Is 2>/dev/null || date -r "$FINISH_EPOCH" -Is 2>/dev/null || echo "in ${HOURS}h")"

echo "Instrument soak: ${HOURS}h @ ${BUFFER}×${PERIODS}, patch=${PATCH_NAME} voices=${VOICES}, condition A"
echo "Started: $(date -Is)"
echo "Expected finish: ${FINISH_ISO}"
echo "Log: ${OUTPUT}"

STAGE=header
{
    echo
    echo "=== measure-soak-instrument buffer=${BUFFER} periods=${PERIODS} patch=${PATCH_NAME} voices=${VOICES} hours=${HOURS} $(date -Is) ==="
    echo "PROVENANCE patch=${PATCH_NAME} hold_voices=${VOICES} buffer=${BUFFER} periods=${PERIODS} condition=A"
    echo "SENTINEL soak-start"
    echo "expected_finish=${FINISH_ISO}"
} >>"$OUTPUT"

# Rule −1: stderr must land in the log — header-only silence is indistinguishable from "still warming up".
exec 2> >(tee -a "$OUTPUT" >&2)

STAGE=env-config
_set_env_var MPE_POLY_GOVERNOR 0
_set_env_var MPE_JACK_SOFTMODE 0
STAGE=services-stop
systemctl stop surge-poly-governor.service 2>/dev/null || true
systemctl stop mpe-looper-session.service sl-watchdog.service mpe-sooperlooper.service 2>/dev/null || true

STAGE=set-surge-audio
if ! "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$BUFFER" --periods "$PERIODS"; then
    echo "ERROR: set-surge-audio failed" >&2
    exit 1
fi
sleep 8

STAGE=jack-restart
systemctl restart mpe-jackd.service
sleep 4
STAGE=jack-wait
mpe_wait_for_jack_server 30
STAGE=surge-restart
systemctl restart surge-xt-cli.service
sleep 6
STAGE=meter-start
systemctl start mpe-peak-meter.service 2>/dev/null || true
sleep 2

STAGE=load-patch
_as_user python3 "$SCRIPT_DIR/load-patch-osc.py" "$PATCH_PATH"
sleep 1

STAGE=start-hold-load
SOAK_SEC=$((HOURS * 3600 + 120))
_as_user python3 "$SCRIPT_DIR/midi-load-hold.py" "$SOAK_SEC" "$VOICES" \
    >"/tmp/instrument-soak-midi.log" 2>&1 &
LOAD_PID=$!
sleep 2

STAGE=meter-baseline
if ! _read_meter_xruns; then
    echo "ERROR: meter blind at soak start" >&2
    exit 1
fi
START_XR="$REPLY"

STAGE=soak-loop
echo "SENTINEL soak-loop-entered" >>"$OUTPUT"

hour=1
minute=0
invalid_windows=0
prev_xr="$START_XR"
HOUR_START="$START_XR"

while [ "$minute" -lt "$TOTAL_MIN" ]; do
    STAGE=soak-loop-minute
    sleep 60
    minute=$((minute + 1))
    if ! _read_meter_xruns; then
        invalid_windows=$((invalid_windows + 1))
        temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"
        echo "SOAK minute=${minute} meter_live=0 INVALID_WINDOW ${temp}" >>"$OUTPUT"
        echo "WARN minute ${minute}: meter blind"
        continue
    fi
    cur="$REPLY"
    if [ "$cur" -lt "$prev_xr" ]; then
        STAGE=soak-loop-meter-reset
        echo "ERROR: meter restarted mid-soak" >&2
        exit 1
    fi
    delta=$((cur - prev_xr))
    prev_xr="$cur"
    temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"
    throttle="$(vcgencmd get_throttled 2>/dev/null || echo 'throttled=unknown')"
    {
        echo "SOAK minute=${minute} xruns_minute=${delta} xruns_total=$((cur - START_XR)) meter_live=1 meter_age_s=${METER_AGE_S} ${temp} ${throttle}"
    } >>"$OUTPUT"

    if [ $((minute % 60)) -eq 0 ]; then
        hour_delta=$((cur - HOUR_START))
        {
            echo "SOAK hour=${hour} minute=${minute} xruns_hour=${hour_delta} xruns_total=$((cur - START_XR)) meter_live=1 invalid_windows=${invalid_windows} ${temp} ${throttle}"
        } >>"$OUTPUT"
        echo "SOAK hour ${hour}: +${hour_delta} xruns (total $((cur - START_XR)))"
        hour=$((hour + 1))
        HOUR_START="$cur"
    fi
done

FINAL=$((prev_xr - START_XR))
STAGE=soak-complete
SOAK_COMPLETE=1
{
    echo "RESULT soak_hours=${HOURS} buffer=${BUFFER} periods=${PERIODS} patch=${PATCH_NAME} voices=${VOICES} xruns_total=${FINAL} invalid_windows=${invalid_windows}"
    echo "SENTINEL soak-complete"
} >>"$OUTPUT"
echo "Instrument soak complete: ${FINAL} xruns in ${HOURS} h → ${OUTPUT}"
