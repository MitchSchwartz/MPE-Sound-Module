#!/bin/bash
# Low-latency measurement harness — Step 0+ of low-latency-512-256-spec.md
#
# Given buffer size and stack condition, runs N windows (default 60 s) with
# deterministic midi-load, recording provenance, verified JACK period, asserted
# sample count, period-jitter histogram from mpe-xrun-probe, and DSP median/p99. Appends to the output file — never truncates.
#
# Usage:
#   sudo ./scripts/measure-latency-run.sh --buffer 512 --condition D --runs 10 --restart-between 5
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
RESTART_BETWEEN=""
MIDI_LOAD_VOICES=75
_SOFTMODE_CHANGED=0
_LOAD_PID=""
_PROBE_PID=""
PROBE_BIN="${MPE_MODULE_REPO}/native/mpe-xrun-probe/mpe-xrun-probe"

while [ $# -gt 0 ]; do
    case "$1" in
        --buffer) BUFFER="${2:?--buffer requires a value}"; shift 2 ;;
        --condition) CONDITION="${2:?--condition requires a value}"; shift 2 ;;
        --runs) RUNS="${2:?--runs requires a value}"; shift 2 ;;
        --seconds) SECONDS_PER_RUN="${2:?--seconds requires a value}"; shift 2 ;;
        --output) OUTPUT="${2:?--output requires a path}"; shift 2 ;;
        --self-test) SELF_TEST=true; SECONDS_PER_RUN=10; RUNS=1; shift ;;
        --restart-between) RESTART_BETWEEN="${2:?--restart-between requires a run index}"; shift 2 ;;
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

_stop_xrun_probe() {
    local logpath="${1:-}"
    if [ -n "$_PROBE_PID" ] && kill -0 "$_PROBE_PID" 2>/dev/null; then
        kill -TERM "$_PROBE_PID" 2>/dev/null || true
    fi
    if [ -n "$logpath" ]; then
        local w=0
        while [ "$w" -lt 50 ]; do
            grep -q '^PROBE_END' "$logpath" 2>/dev/null && break
            sleep 0.1
            w=$((w + 1))
        done
    else
        sleep 0.5
    fi
    if [ -n "$_PROBE_PID" ]; then
        wait "$_PROBE_PID" 2>/dev/null || true
    fi
    _PROBE_PID=""
    pkill -x mpe-xrun-probe 2>/dev/null || true
    sleep 0.2
}

_restore_all() {
    _stop_midi_load
    _stop_xrun_probe
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
        echo "WARNING: MPE_PEAK_METER is not 1 — xrun count uses meter.state only if enabled" >&2
        return 0
    fi
    if ! systemctl is-active --quiet mpe-peak-meter.service 2>/dev/null; then
        systemctl start mpe-peak-meter.service 2>/dev/null || true
        sleep 2
    fi
}

_ensure_xrun_probe() {
    if [ ! -x "$PROBE_BIN" ]; then
        echo "Building mpe-xrun-probe..."
        "$SCRIPT_DIR/build-mpe-xrun-probe.sh" --required
    fi
    [ -x "$PROBE_BIN" ] || {
        echo "ERROR: mpe-xrun-probe missing at $PROBE_BIN" >&2
        return 1
    }
}

_start_xrun_probe() {
    local logpath="$1"
    _stop_xrun_probe "$logpath"
    _as_user "$PROBE_BIN" "$logpath" &
    _PROBE_PID=$!
    sleep 1
    if ! pgrep -x mpe-xrun-probe >/dev/null 2>&1; then
        echo "ERROR: mpe-xrun-probe failed to start" >&2
        return 1
    fi
}

_restart_condition_stack() {
    echo "=== restart full stack between blocks ==="
    case "$CONDITION" in
        A) _set_condition A ;;
        B)
            systemctl restart mpe-sooperlooper.service
            sleep 10
            ;;
        C)
            systemctl restart mpe-sooperlooper.service mpe-looper-session.service
            sleep 12
            ;;
        D)
            systemctl restart mpe-sooperlooper.service mpe-looper-session.service sl-watchdog.service
            sleep 14
            ;;
    esac
    systemctl is-active mpe-sooperlooper.service mpe-looper-session.service sl-watchdog.service 2>/dev/null \
        | paste - - - | awk '{print "  after restart: sooperlooper="$1" session="$2" watchdog="$3}'
}

_delay_stats() {
    local f="$1"
    awk '
        /^JITTER_SUMMARY / {
            for (i = 1; i <= NF; i++) {
                split($i, kv, "=")
                if (kv[1] == "n") n = kv[2] + 0
                if (kv[1] == "median_usec") med = kv[2] + 0
                if (kv[1] == "p99_usec") p99 = kv[2] + 0
                if (kv[1] == "p99_9_usec") p999 = kv[2] + 0
                if (kv[1] == "max_usec") max = kv[2] + 0
            }
        }
        END {
            if (n == 0) { print "0 0 0 0 0"; exit }
            printf "%d %.0f %.0f %.0f %.0f\n", n, med, p99, p999, max
        }
    ' "$f" 2>/dev/null || echo "0 0 0 0 0"
}

_frames_late_stats() {
    local f="$1"
    awk '
        /^FRAMES_LATE_SUMMARY / {
            for (i = 1; i <= NF; i++) {
                split($i, kv, "=")
                if (kv[1] == "p99_usec") p99 = kv[2] + 0
                if (kv[1] == "max_usec") max = kv[2] + 0
            }
        }
        END { printf "%.0f %.0f\n", p99 + 0, max + 0 }
    ' "$f" 2>/dev/null || echo "0 0"
}

_delay_stats_legacy() {
    local f="$1"
    awk '
        /^XRUN wall=/ {
            split($0, parts, "delay_usec=")
            split(parts[2], rest, " ")
            v = rest[1] + 0
            a[++n] = v
        }
        END {
            if (n == 0) { print "0 0 0 0 0"; exit }
            nz = 0
            for (i = 1; i <= n; i++)
                for (j = i + 1; j <= n; j++)
                    if (a[i] > a[j]) { t = a[i]; a[i] = a[j]; a[j] = t }
            for (i = 1; i <= n; i++) if (a[i] > 0) nz++
            med = a[int((n + 1) / 2)]
            p99i = int(n * 0.99); if (p99i < 1) p99i = 1; if (p99i > n) p99i = n
            printf "%d %d %.0f %.0f %.0f\n", n, nz, med, a[p99i], a[n]
        }
    ' "$f" 2>/dev/null || echo "0 0 0 0 0"
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

_run_window() {
    local tag="$1"
    local run_file="$2"
    local dsp_raw="$3"
    local xrun_events="$4"
    local i dsp prev_xr start_xr cur_xr delta samples=0

    : >"$run_file"
    : >"$dsp_raw"
    rm -f "$xrun_events"

    if ! _start_xrun_probe "$xrun_events"; then
        return 1
    fi

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
        prev_xr="$cur_xr"
    done

    _kill_jcl
    _stop_xrun_probe "$xrun_events"

    if [ "$samples" -ne "$SECONDS_PER_RUN" ]; then
        echo "ERROR: sample count $samples != expected $SECONDS_PER_RUN (trap 3)" >&2
        return 1
    fi

    local total_xr=$((prev_xr - start_xr))
    local dsp_median dsp_p99 dsp_max
    local delay_n delay_nz delay_med delay_p99 delay_max
    local jitter_n jitter_med jitter_p99 jitter_p999 jitter_max
    local late_p99 late_max
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
    read -r jitter_n jitter_med jitter_p99 jitter_p999 jitter_max < <(_delay_stats "$xrun_events")
    read -r late_p99 late_max < <(_frames_late_stats "$xrun_events")
    read -r delay_n delay_nz delay_med delay_p99 delay_max < <(_delay_stats_legacy "$xrun_events")

    if [ "$SECONDS_PER_RUN" -ge 30 ] && [ "$jitter_n" -lt 100 ]; then
        echo "ERROR: jitter_n=${jitter_n} — probe process callback produced too few samples" >&2
        return 1
    fi
    temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"
    throttle="$(vcgencmd get_throttled 2>/dev/null || echo 'throttled=unknown')"

    {
        echo "RESULT tag=${tag} xruns=${total_xr} dsp_median=${dsp_median} dsp_p99=${dsp_p99} dsp_max=${dsp_max}"
        echo "RESULT tag=${tag} jitter_n=${jitter_n} jitter_median_usec=${jitter_med} jitter_p99_usec=${jitter_p99} jitter_p99_9_usec=${jitter_p999} jitter_max_usec=${jitter_max}"
        echo "RESULT tag=${tag} frames_late_p99_usec=${late_p99} frames_late_max_usec=${late_max}"
        echo "RESULT tag=${tag} delay_events=${delay_n} delay_nonzero=${delay_nz} (legacy, ignore)"
        echo "RESULT tag=${tag} samples=${samples} ${temp} ${throttle}"
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
    _ensure_xrun_probe || exit 1

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
        [ -s "$xev" ] && { echo "--- xrun delays (probe) ---"; cat "$xev"; }

        if [ -n "$RESTART_BETWEEN" ] && [ "$run_idx" -eq "$RESTART_BETWEEN" ] && [ "$run_idx" -lt "$RUNS" ]; then
            _restart_condition_stack
        fi

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
