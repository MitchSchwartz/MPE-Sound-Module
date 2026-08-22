#!/bin/bash
# Sample jack_cpu_load and report DSP percentiles + absolute ms per period.
#
# Usage:
#   sudo ./scripts/measure-dsp-sample.sh --buffer 1024 --periods 3 --seconds 30 --runs 3 \
#       --tag V1-silence-1024 --output /path/log --surge on
#   sudo ./scripts/measure-dsp-sample.sh ... --surge off   # V2 engine baseline
#
# Requires strict mode (disables softmode for the session). Does not start midi-load.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

BUFFER=""
PERIODS=""
SECONDS_PER_RUN=30
RUNS=3
TAG=""
OUTPUT=""
SURGE="on"
RATE=48000
_SOFTMODE_CHANGED=0
ENV_FILE="/etc/mpe/mpe.env"

while [ $# -gt 0 ]; do
    case "$1" in
        --buffer) BUFFER="${2:?}"; shift 2 ;;
        --periods) PERIODS="${2:?}"; shift 2 ;;
        --seconds) SECONDS_PER_RUN="${2:?}"; shift 2 ;;
        --runs) RUNS="${2:?}"; shift 2 ;;
        --tag) TAG="${2:?}"; shift 2 ;;
        --output) OUTPUT="${2:?}"; shift 2 ;;
        --surge) SURGE="${2:?}"; shift 2 ;;
        -h | --help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "Unknown: $1" >&2; exit 2 ;;
    esac
done

[ -n "$BUFFER" ] && [ -n "$PERIODS" ] && [ -n "$TAG" ] && [ -n "$OUTPUT" ] || {
    echo "ERROR: --buffer --periods --tag --output required" >&2
    exit 2
}

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo" >&2
    exit 1
fi

RUN_AS_USER="${MPE_PI_USER:-mitch}"
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
    _SOFTMODE_CHANGED=1
    systemctl restart mpe-jackd.service
    sleep 4
    mpe_wait_for_jack_server 30
}

_restore_softmode() {
    if [ "$_SOFTMODE_CHANGED" = 1 ]; then
        _set_env_var MPE_JACK_SOFTMODE 1
        _SOFTMODE_CHANGED=0
    fi
}
trap _restore_softmode EXIT INT TERM

_dsp_stats() {
    local raw="$1" period_ms="$2"
    awk -v pms="$period_ms" '
        /[0-9]+\.[0-9]+/ {
            v = $NF + 0
            if (v > 0 && v <= 200) { a[++n] = v }
        }
        END {
            if (n == 0) { print "0 0 0 0 0 0 0 0 0 0"; exit }
            for (i = 1; i <= n; i++) for (j = i + 1; j <= n; j++)
                if (a[i] > a[j]) { t = a[i]; a[i] = a[j]; a[j] = t }
            function pct(p,   i) {
                i = int(n * p); if (i < 1) i = 1; if (i > n) i = n; return a[i]
            }
            med = pct(0.50)
            p999 = pct(0.999)
            p9999 = pct(0.9999)
            mx = a[n]
            printf "%d %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f %.4f\n", \
                n, med, pct(0.99), p999, p9999, mx, \
                med * pms / 100, pct(0.99) * pms / 100, p999 * pms / 100, mx * pms / 100
        }
    ' "$raw"
}

_state_stamp() {
    local card
    card="$("$SCRIPT_DIR/resolve-alsa-playback-status.sh" 2>/dev/null | awk -F= '/^CARD=/{print $2}' || echo '?')"
    echo "STATE period=${BUFFER} nperiods=${PERIODS} rate=${RATE} card=${card} \
governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null) \
mhz=$(( $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || echo 0) / 1000 )) \
softmode=$(grep ^MPE_JACK_SOFTMODE= "$ENV_FILE" 2>/dev/null | cut -d= -f2) \
poly_governor=$(grep ^MPE_POLY_GOVERNOR= "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo unset) \
poly_ceiling=$(grep ^MPE_POLY_CEILING= "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo unset) \
surge=$(systemctl is-active surge-xt-cli 2>/dev/null) \
sha=$(git -C "$MPE_MODULE_REPO" rev-parse --short HEAD 2>/dev/null || echo unknown) \
throttled=$(vcgencmd get_throttled 2>/dev/null) \
temp=$(vcgencmd measure_temp 2>/dev/null)"
}

mpe_source_appliance_env
_enable_strict

if [ "$SURGE" = off ]; then
    systemctl stop surge-xt-cli.service 2>/dev/null || true
    sleep 2
else
    systemctl start surge-xt-cli.service 2>/dev/null || true
    sleep 6
fi

"$SCRIPT_DIR/set-surge-audio.sh" --buffer "$BUFFER" --periods "$PERIODS"
sleep 6

period_ms="$(awk -v b="$BUFFER" -v r="$RATE" 'BEGIN { printf "%.6f", b * 1000 / r }')"

{
    echo "=== measure-dsp-sample tag=${TAG} buffer=${BUFFER} periods=${PERIODS} surge=${SURGE} runs=${RUNS} seconds=${SECONDS_PER_RUN} $(date -Is) ==="
    _state_stamp
    echo "period_deadline_ms=${period_ms}"

    run=1
    while [ "$run" -le "$RUNS" ]; do
        rtag="${TAG}-run${run}"
        raw="/tmp/dsp-${rtag}-$(date +%s).raw"
        : >"$raw"
        _as_user stdbuf -oL jack_cpu_load >"$raw" 2>/dev/null &
        jcl=$!
        sleep "$SECONDS_PER_RUN"
        kill -9 "$jcl" 2>/dev/null || true
        wait "$jcl" 2>/dev/null || true

        read -r n med p99 p999 p9999 mx med_ms p99_ms p999_ms mx_ms < <(_dsp_stats "$raw" "$period_ms")
        temp="$(vcgencmd measure_temp 2>/dev/null || echo '?')"
        thr="$(vcgencmd get_throttled 2>/dev/null || echo '?')"
        echo "RESULT tag=${rtag} dsp_n=${n} dsp_median=${med} dsp_p99=${p99} dsp_p999=${p999} dsp_p9999=${p9999} dsp_max=${mx}"
        echo "RESULT tag=${rtag} dsp_ms_median=${med_ms} dsp_ms_p99=${p99_ms} dsp_ms_p999=${p999_ms} dsp_ms_max=${mx_ms} period_ms=${period_ms} ${temp} ${thr}"
        run=$((run + 1))
        [ "$run" -le "$RUNS" ] && sleep 3
    done
    echo "SENTINEL dsp-sample-end tag=${TAG}"
} >>"$OUTPUT"

echo "Appended to $OUTPUT"
