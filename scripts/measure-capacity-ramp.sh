#!/bin/bash
# Ramp voice count to first graph overrun at one buffer size (V7 cell).
#
# Usage: sudo ./scripts/measure-capacity-ramp.sh --buffer 1024 --periods 3 \
#            --patch-name Crystals --output /path/log --tag V7-a

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

BUFFER=""
PERIODS=3
TAG=""
OUTPUT=""
PATCH_NAME=""
PROBE_SEC=12
CONFIRM_SEC=20
STEP=2
MAX_VOICES=32
RATE=48000
ENV_FILE="/etc/mpe/mpe.env"
RUN_AS_USER="${MPE_PI_USER:-mitch}"

while [ $# -gt 0 ]; do
    case "$1" in
        --buffer) BUFFER="${2:?}"; shift 2 ;;
        --periods) PERIODS="${2:?}"; shift 2 ;;
        --tag) TAG="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        --patch-name) PATCH_NAME="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,8p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

[ -n "$BUFFER" ] && [ -n "$TAG" ] && [ -n "$OUTPUT" ] || {
    echo "ERROR: --buffer --tag --output required" >&2
    exit 2
}

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

_as_user() { sudo -u "$RUN_AS_USER" -- "$@"; }

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

_enable_strict() {
    _set_env_var MPE_JACK_SOFTMODE 0
    systemctl restart mpe-jackd.service
    sleep 4
    mpe_wait_for_jack_server 30
    systemctl restart surge-xt-cli.service
    sleep 6
}

_xruns_delta() {
    local voices="$1" secs="$2"
    local start end
    if ! systemctl is-active --quiet mpe-peak-meter.service 2>/dev/null; then
        systemctl start mpe-peak-meter.service
        sleep 2
    fi
    start="$(mpe_meter_xruns_read)" || start=0
    _as_user python3 "$SCRIPT_DIR/midi-load-hold.py" "$secs" "$voices" \
        >/tmp/midi-load-hold-$$.log 2>&1 &
    local pid=$!
    sleep "$secs"
    wait "$pid" 2>/dev/null || true
    end="$(mpe_meter_xruns_read)" || end=0
    echo $((end - start))
}

_confirm_window() {
    local voices="$1" raw="/tmp/capacity-confirm-${TAG}-${voices}.raw"
    local period_ms jcl
    period_ms="$(awk -v b="$BUFFER" -v r="$RATE" 'BEGIN { printf "%.6f", b * 1000 / r }')"
    : >"$raw"
    _as_user python3 "$SCRIPT_DIR/midi-load-hold.py" "$((CONFIRM_SEC + 2))" "$voices" \
        >/tmp/midi-load-hold-confirm-$$.log 2>&1 &
    local load_pid=$!
    sleep 2
    _as_user stdbuf -oL jack_cpu_load >"$raw" 2>/dev/null &
    jcl=$!
    sleep "$CONFIRM_SEC"
    kill -9 "$jcl" 2>/dev/null || true
    wait "$jcl" 2>/dev/null || true
    wait "$load_pid" 2>/dev/null || true
    awk -v pms="$period_ms" '
        /[0-9]+\.[0-9]+/ { v = $NF + 0; if (v > 0 && v <= 200) a[++n] = v }
        END {
            if (n == 0) { print "0 0 0 0 0 0"; exit }
            for (i = 1; i <= n; i++) for (j = i + 1; j <= n; j++)
                if (a[i] > a[j]) { t = a[i]; a[i] = a[j]; a[j] = t }
            p999_i = int(n * 0.999); if (p999_i < 1) p999_i = 1
            p9999_i = int(n * 0.9999); if (p9999_i < 1) p9999_i = 1
            printf "%d %.4f %.4f %.4f %.4f %.4f %.4f\n", n, a[int((n+1)/2)], a[int(n*0.99)], a[p999_i], a[p9999_i], a[n], a[int((n+1)/2)]*pms/100, a[p999_i]*pms/100
        }
    ' "$raw"
}

mpe_source_appliance_env
_set_env_var MPE_POLY_GOVERNOR 0
_set_env_var MPE_POLY_CEILING 64
_set_env_var MPE_POLY_FLOOR 64
systemctl stop surge-poly-governor.service 2>/dev/null || true
_enable_strict
"$SCRIPT_DIR/set-surge-audio.sh" --buffer "$BUFFER" --periods "$PERIODS"
sleep 6

{
    echo "=== capacity-ramp tag=${TAG} buffer=${BUFFER}x${PERIODS} patch=${PATCH_NAME:-unknown} $(date -Is) ==="
    echo "CARD=$("$SCRIPT_DIR/resolve-alsa-playback-status.sh" 2>/dev/null | awk -F= '/^CARD=/{print $2}')"

    last_clean=0
    first_overrun=""
    v=$STEP
    while [ "$v" -le "$MAX_VOICES" ]; do
        xr="$(_xruns_delta "$v" "$PROBE_SEC")"
        echo "PROBE voices=${v} sec=${PROBE_SEC} xruns_delta=${xr}"
        if [ "$xr" -gt 0 ]; then
            first_overrun=$v
            break
        fi
        last_clean=$v
        v=$((v + STEP))
    done

    if [ -z "$first_overrun" ]; then
        echo "RESULT tag=${TAG} first_overrun=none_below_${MAX_VOICES} sustained_clean=${last_clean}"
    else
        echo "RESULT tag=${TAG} first_overrun=${first_overrun} sustained_clean=${last_clean}"
    fi

    if [ "$last_clean" -gt 0 ]; then
        read -r cn cmed cp99 cp999 cp9999 cmax cms_med cms_p999 < <(_confirm_window "$last_clean")
        echo "RESULT tag=${TAG} confirm_voices=${last_clean} dsp_n=${cn} dsp_ms_median=${cms_med} dsp_ms_p999=${cms_p999} dsp_p999=${cp999} dsp_max=${cmax}"
    fi
    echo "SENTINEL capacity-ramp-end tag=${TAG}"
} >>"$OUTPUT"

echo "Appended capacity ramp to $OUTPUT"
