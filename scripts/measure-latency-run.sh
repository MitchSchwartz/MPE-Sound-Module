#!/bin/bash
# Low-latency measurement harness — Step 0+ of low-latency-512-256-spec.md
#
# Given buffer size and stack condition, runs N windows (default 60 s) with
# deterministic midi-load, recording provenance, verified JACK period, asserted
# sample count, per-xrun delay from the jackd journal, and DSP median/p99.
# Appends to the output file — never truncates.
#
# Usage:
#   sudo ./scripts/measure-latency-run.sh --buffer 512 --condition D --runs 5
#   sudo ./scripts/measure-latency-run.sh --buffer 512 --condition A --runs 1 --seconds 10 --self-test
#
# Conditions (cumulative stack):
#   A — baseline (all looper units stopped)
#   B — mpe-sooperlooper only
#   C — + mpe-looper-session
#   D — + sl-watchdog (full stack)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

BUFFER=""
CONDITION=""
RUNS=3
SECONDS_PER_RUN=60
OUTPUT="${MPE_LATENCY_LOG:-$HOME/latency-measure.log}"
SELF_TEST=false
RESTORE_BUFFER=""
MIDI_LOAD_VOICES=75
_SOFTMODE_CHANGED=0
_LOAD_PID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --buffer) BUFFER="${2:?--buffer requires a value}"; shift 2 ;;
        --condition) CONDITION="${2:?--condition requires a value}"; shift 2 ;;
        --runs) RUNS="${2:?--runs requires a value}"; shift 2 ;;
        --seconds) SECONDS_PER_RUN="${2:?--seconds requires a value}"; shift 2 ;;
        --output) OUTPUT="${2:?--output requires a path}"; shift 2 ;;
        --self-test) SELF_TEST=true; SECONDS_PER_RUN=10; RUNS=1; shift ;;
        -h | --help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done

if [ -z "$BUFFER" ] || [ -z "$CONDITION" ]; then
    echo "ERROR: --buffer and --condition are required" >&2
    exit 2
fi

case "$CONDITION" in
    A | B | C | D) ;;
    *) echo "ERROR: condition must be A, B, C, or D" >&2; exit 2 ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run with sudo so set-surge-audio.sh can persist buffer size" >&2
    exit 1
fi

mpe_source_appliance_env
RESTORE_BUFFER="$(mpe_jack_period)"

RUN_AS_USER="${MPE_PI_USER:-mitch}"
if [ "$(id -u)" -eq 0 ] && id "$RUN_AS_USER" >/dev/null 2>&1; then
    _as_user() { sudo -u "$RUN_AS_USER" -- "$@"; }
else
    _as_user() { "$@"; }
fi

ENV_FILE="/etc/mpe/mpe.env"

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

_restore_softmode() {
    if [ "$_SOFTMODE_CHANGED" = 1 ]; then
        _set_env_var MPE_JACK_SOFTMODE 1
        _SOFTMODE_CHANGED=0
    fi
}

_stop_midi_load() {
    if [ -n "$_LOAD_PID" ] && kill -0 "$_LOAD_PID" 2>/dev/null; then
        kill "$_LOAD_PID" 2>/dev/null || true
        wait "$_LOAD_PID" 2>/dev/null || true
    fi
    _LOAD_PID=""
}

_restore_all() {
    _stop_midi_load
    _restore_softmode
    if [ -n "$RESTORE_BUFFER" ] && [ "$RESTORE_BUFFER" != "$(mpe_jack_period 2>/dev/null || echo "$RESTORE_BUFFER")" ]; then
        "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$RESTORE_BUFFER" >/dev/null 2>&1 || true
    fi
}
trap _restore_all EXIT INT TERM HUP

_jack_period_from_proc() {
    local pid
    pid="$(pgrep -x jackd | head -1)" || return 1
    tr '\0' '\n' < "/proc/$pid/cmdline" | awk '/^-p$/{getline; print; exit}'
}

_assert_jack_period() {
    local want="$1" got
    got="$(_jack_period_from_proc)" || {
        echo "ERROR: jackd not running — cannot verify period" >&2
        return 1
    }
    if [ "$got" != "$want" ]; then
        echo "ERROR: JACK period is $got, expected $want (trap 5 — env write may have failed)" >&2
        return 1
    fi
    echo "jackd period=$got ok"
    return 0
}

_record_provenance() {
    echo "=== provenance $(date -Is) ==="
    if [ -d "$MPE_MODULE_REPO/.git" ]; then
        git -C "$MPE_MODULE_REPO" log --oneline -1 2>/dev/null || true
        git -C "$MPE_MODULE_REPO" status --porcelain 2>/dev/null || true
    fi
    uname -r
    tr '\0' ' ' < /proc/cmdline 2>/dev/null || true
    echo
    for gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        [ -f "$gov" ] || continue
        echo "governor $(basename "$(dirname "$gov")")=$(cat "$gov")"
        break
    done
    ps -o args= -C jackd 2>/dev/null | head -1 || true
}

_set_condition() {
    case "$1" in
        A)
            systemctl stop mpe-looper-session.service sl-watchdog.service mpe-sooperlooper.service 2>/dev/null || true
            ;;
        B)
            systemctl stop mpe-looper-session.service sl-watchdog.service 2>/dev/null || true
            systemctl start mpe-sooperlooper.service
            sleep 6
            ;;
        C)
            systemctl stop sl-watchdog.service 2>/dev/null || true
            systemctl start mpe-sooperlooper.service
            sleep 6
            systemctl start mpe-looper-session.service
            sleep 6
            ;;
        D)
            systemctl start mpe-sooperlooper.service
            sleep 6
            systemctl start mpe-looper-session.service
            sleep 6
            systemctl start sl-watchdog.service
            sleep 8
            ;;
    esac
    echo "condition $1 units:"
    systemctl is-active mpe-sooperlooper.service mpe-looper-session.service sl-watchdog.service 2>/dev/null \
        | paste - - - | awk '{print "  sooperlooper="$1" session="$2" watchdog="$3}'
}

_ensure_peak_meter() {
    if [ "$(mpe_read_appliance_env_var MPE_PEAK_METER 2>/dev/null || echo 0)" != "1" ]; then
        echo "WARNING: MPE_PEAK_METER is not 1 — xrun count falls back to journal only" >&2
        return 0
    fi
    if ! systemctl is-active --quiet mpe-peak-meter.service 2>/dev/null; then
        systemctl start mpe-peak-meter.service 2>/dev/null || true
        sleep 2
    fi
}

_enable_strict_xrun_reporting() {
    _set_env_var MPE_JACK_SOFTMODE 0
    _SOFTMODE_CHANGED=1
    systemctl restart mpe-jackd.service
    sleep 4
    mpe_wait_for_jack_server 30 || return 1
    systemctl restart surge-xt-cli.service 2>/dev/null || true
    sleep 6
    _set_condition "$CONDITION"
    return 0
}

_meter_xruns() {
    grep -oP '(?<=^xruns=)[0-9]+' /run/mpe/meter.state 2>/dev/null || echo 0
}

_journal_xrun_cursor=""
_journal_xrun_total=0

_init_journal_cursor() {
    _journal_xrun_cursor=""
    _journal_xrun_total=0
    local out
    out="$(journalctl -u mpe-jackd.service --no-pager --show-cursor --since "1 second ago" 2>/dev/null || true)"
    _journal_xrun_cursor="$(printf '%s\n' "$out" | grep '^-- cursor:' | tail -1 | sed 's/^-- cursor: //')"
}

_poll_journal_xruns() {
    local argv out new_cursor line delay_usec wall
    if [ -n "$_journal_xrun_cursor" ]; then
        argv=(journalctl -u mpe-jackd.service --no-pager --show-cursor --after-cursor "$_journal_xrun_cursor")
    else
        argv=(journalctl -u mpe-jackd.service --no-pager --show-cursor --since "2 seconds ago")
    fi
    out="$("${argv[@]}" 2>/dev/null || true)"
    new_cursor="$(printf '%s\n' "$out" | grep '^-- cursor:' | tail -1 | sed 's/^-- cursor: //')"
    [ -n "$new_cursor" ] && _journal_xrun_cursor="$new_cursor"
    while IFS= read -r line; do
        if echo "$line" | grep -qi xrun; then
            _journal_xrun_total=$((_journal_xrun_total + 1))
            wall="$(date -Is)"
            if [[ "$line" =~ [Xx]run\ of\ at\ least\ ([0-9]+)\ msecs ]]; then
                delay_usec=$((BASH_REMATCH[1] * 1000))
            else
                delay_usec=-1
            fi
            printf 'XRUN_EVENT wall=%s delay_usec=%s line=%s\n' "$wall" "$delay_usec" "$line"
        fi
    done < <(printf '%s\n' "$out" | grep -vi '^-- cursor:')
}

_run_window() {
    local tag="$1"
    local run_file="$2"
    local dsp_raw="$3"
    local xrun_events="$4"
    local i dsp prev_xr start_xr cur_xr delta samples=0

    : >"$run_file"
    : >"$dsp_raw"
    : >"$xrun_events"

    _init_journal_cursor
    _as_user stdbuf -oL jack_cpu_load >"$dsp_raw" 2>/dev/null &
    local jcl=$!
    _kill_jcl() { kill -9 "$jcl" 2>/dev/null || true; wait "$jcl" 2>/dev/null || true; }

    prev_xr="$(_meter_xruns)"
    start_xr="$prev_xr"

    printf '  %4s %8s %8s %7s\n' "t" "dsp%" "xruns" "delta" >>"$run_file"

    for ((i = 1; i <= SECONDS_PER_RUN; i++)); do
        sleep 1
        samples=$((samples + 1))
        dsp="$(tail -1 "$dsp_raw" 2>/dev/null | grep -oP '[0-9]+\.[0-9]+' | head -1 || echo '?')"
        cur_xr="$(_meter_xruns)"
        delta=$((cur_xr - prev_xr))
        mark=""
        [ "$delta" -gt 0 ] && mark=" <<< XRUN x$delta"
        printf '  %4d %8s %8s %7d%s\n' "$i" "${dsp:-?}" "$cur_xr" "$delta" >>"$run_file"
        _poll_journal_xruns >>"$xrun_events"
        prev_xr="$cur_xr"
    done

    _kill_jcl

    if [ "$samples" -ne "$SECONDS_PER_RUN" ]; then
        echo "ERROR: sample count $samples != expected $SECONDS_PER_RUN (trap 3)" >&2
        return 1
    fi

    local total_xr=$((prev_xr - start_xr))
    local dsp_median dsp_p99 dsp_max
    read -r dsp_median dsp_p99 dsp_max < <(
        awk '
            /^[[:space:]]+[0-9]+/ {
                v=$2; if (v != "?") { a[++n]=v+0 }
            }
            END {
                if (n==0) { print "0 0 0"; exit }
                for (i=1;i<=n;i++) {
                    for (j=i+1;j<=n;j++) if (a[i]>a[j]) { t=a[i]; a[i]=a[j]; a[j]=t }
                }
                med=a[int((n+1)/2)]
                p99i=int(n*0.99); if (p99i<1) p99i=1; if (p99i>n) p99i=n
                p99=a[p99i]
                max=a[n]
                printf "%.6f %.6f %.6f\n", med, p99, max
            }
        ' "$run_file"
    )

    local temp throttle
    temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"
    throttle="$(vcgencmd get_throttled 2>/dev/null || echo 'throttled=unknown')"

    {
        echo "RESULT tag=${tag} xruns=${total_xr} dsp_median=${dsp_median} dsp_p99=${dsp_p99} dsp_max=${dsp_max}"
        echo "RESULT tag=${tag} samples=${samples} journal_xruns=${_journal_xrun_total} ${temp} ${throttle}"
        echo "RESULT tag=${tag} file=${run_file} xrun_events=${xrun_events}"
    }

    echo "SENTINEL run-complete tag=${tag} xruns=${total_xr}"
    return 0
}

{
    echo
    echo "=== measure-latency-run buffer=${BUFFER} condition=${CONDITION} runs=${RUNS} seconds=${SECONDS_PER_RUN} $(date -Is) ==="
    echo "SENTINEL harness-start"
    _ensure_peak_meter

    if ! "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$BUFFER"; then
        echo "ERROR: set-surge-audio.sh --buffer $BUFFER failed" >&2
        exit 1
    fi
    mpe_source_appliance_env
    sleep 8
    _assert_jack_period "$BUFFER" || exit 1

    if ! _enable_strict_xrun_reporting; then
        echo "ERROR: could not restart jackd for strict xrun reporting" >&2
        exit 1
    fi
    _assert_jack_period "$BUFFER" || exit 1
    echo "=== provenance after strict restart ==="
    _record_provenance

    run_idx=1
    while [ "$run_idx" -le "$RUNS" ]; do
        tag="${CONDITION}-run${run_idx}"
        echo "=== run ${tag} ==="
        stamp="$(date +%s)"
        run_file="/tmp/latency-${tag}-${stamp}.out"
        dsp_raw="/tmp/latency-${tag}-${stamp}.dsp"
        xev="/tmp/latency-${tag}-${stamp}.xruns"

        _as_user python3 "$SCRIPT_DIR/midi-load.py" "$((SECONDS_PER_RUN + 20))" \
            >"/tmp/latency-midi-load-${stamp}.log" 2>&1 &
        _LOAD_PID=$!
        sleep 8

        if ! _run_window "$tag" "$run_file" "$dsp_raw" "$xev"; then
            _stop_midi_load
            exit 1
        fi
        _stop_midi_load

        if [ ! -s "$run_file" ]; then
            echo "ERROR: empty run file $run_file" >&2
            exit 1
        fi
        cat "$run_file"
        [ -s "$xev" ] && { echo "--- xrun events ---"; cat "$xev"; }

        run_idx=$((run_idx + 1))
        [ "$run_idx" -le "$RUNS" ] && sleep 5
    done

    echo "=== restore buffer ${RESTORE_BUFFER} ==="
    "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$RESTORE_BUFFER" || true
    sleep 6
    _assert_jack_period "$RESTORE_BUFFER" || echo "WARNING: restore period check failed" >&2
    echo "done commit=$(git -C "$MPE_MODULE_REPO" rev-parse --short HEAD 2>/dev/null || echo unknown) log=$OUTPUT"
    echo "SENTINEL harness-end"
    echo
} >>"$OUTPUT"

echo "Appended measurement to $OUTPUT"
echo "SENTINEL harness-logged"

if [ "$SELF_TEST" = true ]; then
    if ! grep -q 'SENTINEL harness-end' "$OUTPUT"; then
        echo "SELF-TEST FAIL: missing harness-end sentinel" >&2
        exit 1
    fi
    if ! grep -q 'RESULT tag=' "$OUTPUT"; then
        echo "SELF-TEST FAIL: no RESULT lines" >&2
        exit 1
    fi
    echo "SELF-TEST PASS"
fi

exit 0
