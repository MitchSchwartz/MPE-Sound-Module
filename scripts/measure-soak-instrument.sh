#!/bin/bash
# Instrument-only long-window hold — Gate 1 overnight soak (default) or V12 certification arm.
#
# Default: Cloud Horn @ 5 voices, 1024×2, 8 h, governor off.
#
# Usage:
#   sudo ./scripts/measure-soak-instrument.sh [--hours 8] [--minutes 30] [--output FILE] \
#       [--patch-name "Cloud Horn"] [--voices 5] [--buffer 1024] [--periods 2] \
#       [--governor on|off] [--label TAG]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"
# shellcheck source=lib/measurement-result.sh
source "$SCRIPT_DIR/lib/measurement-result.sh"

HOURS=8
MINUTES=0
OUTPUT="${MPE_SOAK_LOG:-$HOME/instrument-soak-1024x2.log}"
PATCH_NAME="Cloud Horn"
VOICES=5
BUFFER=1024
PERIODS=2
GOVERNOR=off
RUN_LABEL=""
ENV_FILE="/etc/mpe/mpe.env"
RUN_AS_USER="${MPE_PI_USER:-mitch}"
USER_HOME="$(getent passwd "$RUN_AS_USER" | cut -d: -f6)"
QUICK_SELECT="${USER_HOME}/Documents/Surge XT/Patches/Quick Select"
LOAD_PID=""
DSP_PID=""
DSP_RAW=""

while [ $# -gt 0 ]; do
    case "$1" in
        --hours) HOURS="${2:?}"; shift 2 ;;
        --minutes) MINUTES="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        --patch-name) PATCH_NAME="${2:?}"; shift 2 ;;
        --voices) VOICES="${2:?}"; shift 2 ;;
        --buffer) BUFFER="${2:?}"; shift 2 ;;
        --periods) PERIODS="${2:?}"; shift 2 ;;
        --governor) GOVERNOR="${2:?}"; shift 2 ;;
        --label) RUN_LABEL="${2:?}"; shift 2 ;;
        -h | --help)
            sed -n '2,14p' "$0"
            echo "  --governor on|off   default off (B2); V12 uses on"
            echo "  --minutes N         short certification window (mutually preferred over --hours)"
            echo "  --label TAG         optional tag in log header / RESULT"
            exit 0
            ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

case "$GOVERNOR" in
    on | off) ;;
    *) echo "ERROR: --governor must be on or off (got: $GOVERNOR)" >&2; exit 2 ;;
esac

if [ "$MINUTES" -gt 0 ] && [ "$HOURS" -ne 8 ]; then
    echo "WARN: --minutes takes precedence over --hours" >&2
fi

if [ "$MINUTES" -gt 0 ]; then
    TOTAL_MIN="$MINUTES"
else
    TOTAL_MIN=$((HOURS * 60))
fi

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

_env_readback() {
    local key="$1"
    mpe_read_appliance_env_var "$key" 2>/dev/null || echo unset
}

_provenance_line() {
    local svc
    svc="$(systemctl is-active surge-poly-governor.service 2>/dev/null || echo inactive)"
    printf 'PROVENANCE patch=%s hold_voices=%s buffer=%s periods=%s condition=A governor=%s' \
        "$PATCH_NAME" "$VOICES" "$BUFFER" "$PERIODS" "$GOVERNOR"
    printf ' MPE_POLY_GOVERNOR=%s' "$(_env_readback MPE_POLY_GOVERNOR)"
    printf ' MPE_POLY_CPU_HIGH=%s' "$(_env_readback MPE_POLY_CPU_HIGH)"
    printf ' MPE_POLY_CPU_LOW=%s' "$(_env_readback MPE_POLY_CPU_LOW)"
    printf ' MPE_POLY_CPU_HIGH_HOLD_S=%s' "$(_env_readback MPE_POLY_CPU_HIGH_HOLD_S)"
    printf ' MPE_POLY_CPU_LOW_HOLD_S=%s' "$(_env_readback MPE_POLY_CPU_LOW_HOLD_S)"
    printf ' MPE_POLY_GOVERNOR_HEADROOM=%s' "$(_env_readback MPE_POLY_GOVERNOR_HEADROOM)"
    printf ' surge-poly-governor=%s' "$svc"
    if [ -n "$RUN_LABEL" ]; then
        printf ' label=%s' "$RUN_LABEL"
    fi
    printf '\n'
}

# Limit drops only — recover/step-up is release, not engagement (G2 negative control).
_count_governor_engagements_since() {
    local since="$1"
    journalctl -u surge-poly-governor.service --since "$since" --no-pager -o cat 2>/dev/null \
        | grep -cE 'poly-governor: [0-9]+ -> [0-9]+ reason=(emergency|high|spike|warm)' || true
}

_kill_dsp_sampler() {
    if [ -n "${DSP_PID:-}" ] && kill -0 "$DSP_PID" 2>/dev/null; then
        kill "$DSP_PID" 2>/dev/null || true
        if command -v timeout >/dev/null 2>&1; then
            timeout -k 0.5 2 tail --pid="$DSP_PID" -f /dev/null >/dev/null 2>&1 || true
        else
            wait "$DSP_PID" 2>/dev/null || true
        fi
    fi
    DSP_PID=""
}

_dsp_stats() {
    local raw="$1"
    awk '
        function take(v) {
            if (v != "?" && v+0 > 0 && v+0 <= 200) { a[++n]=v+0 }
        }
        /^[[:space:]]+[0-9]+/ { take($2) }
        /^jack DSP load / { take($NF) }
        END {
            if (n==0) { print "0 0"; exit 1 }
            for (i=1;i<=n;i++) {
                for (j=i+1;j<=n;j++) if (a[i]>a[j]) { t=a[i]; a[i]=a[j]; a[j]=t }
            }
            med=a[int((n+1)/2)]
            max=a[n]
            printf "%.6f %.6f\n", med, max
        }
    ' "$raw"
}

STAGE=init
SOAK_COMPLETE=0

_cleanup() {
    local rc=$?
    _kill_dsp_sampler
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

FINISH_EPOCH=$(( $(date +%s) + TOTAL_MIN * 60 ))
FINISH_ISO="$(date -d "@${FINISH_EPOCH}" -Is 2>/dev/null || date -r "$FINISH_EPOCH" -Is 2>/dev/null || echo "in ${TOTAL_MIN}m")"

if [ "$MINUTES" -gt 0 ]; then
    DURATION_DESC="${MINUTES} min"
else
    DURATION_DESC="${HOURS} h"
fi

echo "Instrument soak: ${DURATION_DESC} @ ${BUFFER}×${PERIODS}, patch=${PATCH_NAME} voices=${VOICES}, governor=${GOVERNOR}, condition A"
echo "Started: $(date -Is)"
echo "Expected finish: ${FINISH_ISO}"
echo "Log: ${OUTPUT}"

STAGE=header
{
    echo
    if [ "$MINUTES" -gt 0 ]; then
        echo "=== measure-soak-instrument buffer=${BUFFER} periods=${PERIODS} patch=${PATCH_NAME} voices=${VOICES} minutes=${MINUTES} governor=${GOVERNOR} $(date -Is) ==="
    else
        echo "=== measure-soak-instrument buffer=${BUFFER} periods=${PERIODS} patch=${PATCH_NAME} voices=${VOICES} hours=${HOURS} governor=${GOVERNOR} $(date -Is) ==="
    fi
    _provenance_line
    echo "SENTINEL soak-start"
    echo "expected_finish=${FINISH_ISO}"
} >>"$OUTPUT"

# Rule −1: stderr must land in the log — header-only silence is indistinguishable from "still warming up".
exec 2> >(tee -a "$OUTPUT" >&2)

STAGE=env-config
_set_env_var MPE_JACK_SOFTMODE 0
if [ "$GOVERNOR" = on ]; then
    _set_env_var MPE_POLY_GOVERNOR 1
else
    _set_env_var MPE_POLY_GOVERNOR 0
fi

STAGE=services-stop
systemctl stop mpe-looper-session.service sl-watchdog.service mpe-sooperlooper.service 2>/dev/null || true
systemctl stop touch-patch-browser.service patch-browser.service 2>/dev/null || true
if [ "$GOVERNOR" = off ]; then
    systemctl stop surge-poly-governor.service 2>/dev/null || true
fi

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
if [ "$GOVERNOR" = on ]; then
    STAGE=governor-start
    systemctl enable surge-poly-governor.service 2>/dev/null || true
    systemctl restart surge-poly-governor.service
    GOV_SINCE="$(date -Is)"
    sleep 1
fi
STAGE=meter-start
systemctl start mpe-peak-meter.service 2>/dev/null || true
sleep 2

STAGE=load-patch
_as_user python3 "$SCRIPT_DIR/load-patch-osc.py" "$PATCH_PATH"
sleep 1

STAGE=start-hold-load
SOAK_SEC=$((TOTAL_MIN * 60 + 120))
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

DSP_RAW="${OUTPUT}.dsp"
_as_user stdbuf -oL jack_cpu_load >"$DSP_RAW" 2>/dev/null &
DSP_PID=$!

STAGE=soak-loop
echo "SENTINEL soak-loop-entered" >>"$OUTPUT"

hour=1
minute=0
invalid_windows=0
prev_xr="$START_XR"
HOUR_START="$START_XR"
GOV_TOTAL=0
LOOP_START_EPOCH="$(date +%s)"

while [ "$minute" -lt "$TOTAL_MIN" ]; do
    STAGE=soak-loop-minute
    sleep 60
    minute=$((minute + 1))
    minute_since="$(date -d "@$((LOOP_START_EPOCH + (minute - 1) * 60))" -Is 2>/dev/null \
        || date -r "$((LOOP_START_EPOCH + (minute - 1) * 60))" -Is 2>/dev/null \
        || date -Is)"
    gov_delta=0
    if [ "$GOVERNOR" = on ]; then
        gov_delta="$(_count_governor_engagements_since "$minute_since")"
        GOV_TOTAL=$((GOV_TOTAL + gov_delta))
    fi
    if ! _read_meter_xruns; then
        invalid_windows=$((invalid_windows + 1))
        temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"
        echo "SOAK minute=${minute} meter_live=0 INVALID_WINDOW governor_engagements=${gov_delta} ${temp}" >>"$OUTPUT"
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
        echo "SOAK minute=${minute} xruns_minute=${delta} xruns_total=$((cur - START_XR)) meter_live=1 meter_age_s=${METER_AGE_S} governor_engagements=${gov_delta} ${temp} ${throttle}"
    } >>"$OUTPUT"

    if [ $((minute % 60)) -eq 0 ]; then
        hour_delta=$((cur - HOUR_START))
        {
            echo "SOAK hour=${hour} minute=${minute} xruns_hour=${hour_delta} xruns_total=$((cur - START_XR)) meter_live=1 invalid_windows=${invalid_windows} governor_engagements_total=${GOV_TOTAL} ${temp} ${throttle}"
        } >>"$OUTPUT"
        echo "SOAK hour ${hour}: +${hour_delta} xruns (total $((cur - START_XR)))"
        hour=$((hour + 1))
        HOUR_START="$cur"
    fi
done

_kill_dsp_sampler

if [ "$GOVERNOR" = on ] && [ -n "${GOV_SINCE:-}" ]; then
    GOV_TOTAL="$(_count_governor_engagements_since "$GOV_SINCE")"
fi

FINAL=$((prev_xr - START_XR))
DSP_MED="unknown"
DSP_MAX="unknown"
if [ -f "$DSP_RAW" ]; then
    if read -r DSP_MED DSP_MAX < <(_dsp_stats "$DSP_RAW"); then
        :
    else
        echo "WARN: no DSP samples in ${DSP_RAW}" >&2
        DSP_MED="unknown"
        DSP_MAX="unknown"
    fi
fi

STAGE=soak-complete
SOAK_COMPLETE=1
{
    if [ "$MINUTES" -gt 0 ]; then
        echo "RESULT soak_minutes=${MINUTES} buffer=${BUFFER} periods=${PERIODS} patch=${PATCH_NAME} voices=${VOICES} governor=${GOVERNOR} xruns_total=${FINAL} invalid_windows=${invalid_windows} dsp_median=${DSP_MED} dsp_max=${DSP_MAX} governor_engagements_total=${GOV_TOTAL}"
    else
        echo "RESULT soak_hours=${HOURS} buffer=${BUFFER} periods=${PERIODS} patch=${PATCH_NAME} voices=${VOICES} governor=${GOVERNOR} xruns_total=${FINAL} invalid_windows=${invalid_windows} dsp_median=${DSP_MED} dsp_max=${DSP_MAX} governor_engagements_total=${GOV_TOTAL}"
    fi
    if [ -n "$RUN_LABEL" ]; then
        echo "RESULT label=${RUN_LABEL} buffer=${BUFFER} periods=${PERIODS} xruns_total=${FINAL}"
    fi
    echo "SENTINEL soak-complete"
} >>"$OUTPUT"
echo "Instrument soak complete: ${FINAL} xruns in ${DURATION_DESC}, governor_engagements=${GOV_TOTAL} → ${OUTPUT}"
