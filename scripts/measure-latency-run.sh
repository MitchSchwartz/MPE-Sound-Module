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
PERIODS=""
RUNS=3
SECONDS_PER_RUN=60
OUTPUT="${MPE_LATENCY_LOG:-$HOME/latency-measure.log}"
SELF_TEST=false
RESTORE_BUFFER=""
RESTORE_PERIODS=""
RESTART_BETWEEN=""
MIDI_LOAD_VOICES=75
HOLD_VOICES=0
PROVENANCE_PATCH=""
PROVENANCE_VOICES=""
PLAYING_LOOPS=0
_SOFTMODE_CHANGED=0
_LOAD_PID=""
_PROBE_PID=""
PROBE_BIN="${MPE_MODULE_REPO}/native/mpe-xrun-probe/mpe-xrun-probe"

SKIP_BUFFER_RESTORE=false
FILL_LOG=""
_FILL_PID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --buffer) BUFFER="${2:?--buffer requires a value}"; shift 2 ;;
        --periods) PERIODS="${2:?--periods requires a value}"; shift 2 ;;
        --condition) CONDITION="${2:?--condition requires a value}"; shift 2 ;;
        --runs) RUNS="${2:?--runs requires a value}"; shift 2 ;;
        --seconds) SECONDS_PER_RUN="${2:?--seconds requires a value}"; shift 2 ;;
        --output) OUTPUT="${2:?--output requires a path}"; shift 2 ;;
        --fill-log) FILL_LOG="${2:?--fill-log requires a path}"; shift 2 ;;
        --self-test) SELF_TEST=true; SECONDS_PER_RUN=10; RUNS=1; shift ;;
        --restart-between) RESTART_BETWEEN="${2:?--restart-between requires a run index}"; shift 2 ;;
        --playing-loops) PLAYING_LOOPS="${2:?--playing-loops requires 0|4|8|16}"; shift 2 ;;
        --hold-voices) HOLD_VOICES="${2:?--hold-voices requires N}"; shift 2 ;;
        --provenance-patch) PROVENANCE_PATCH="${2:?}"; shift 2 ;;
        --provenance-voices) PROVENANCE_VOICES="${2:?}"; shift 2 ;;
        --no-restore-buffer) SKIP_BUFFER_RESTORE=true; shift ;;
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
RESTORE_PERIODS="$(mpe_jack_periods)"

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

_stop_fill_poller() {
    if [ -n "$_FILL_PID" ] && kill -0 "$_FILL_PID" 2>/dev/null; then
        kill -TERM "$_FILL_PID" 2>/dev/null || true
        wait "$_FILL_PID" 2>/dev/null || true
    fi
    _FILL_PID=""
}

_start_fill_poller() {
    local logpath="$1"
    local status_file="$2"
    local seconds="$3"
    _stop_fill_poller
    rm -f "$logpath"
    taskset -c 1 nice -n 19 "$SCRIPT_DIR/mpe-fill-poller.sh" \
        "$status_file" "$logpath" "$seconds" &
    _FILL_PID=$!
    sleep 0.2
    if ! kill -0 "$_FILL_PID" 2>/dev/null; then
        echo "ERROR: mpe-fill-poller failed to start" >&2
        return 1
    fi
    return 0
}

# Instrument 2 — jackd ALSA xrun magnitudes (post-window journal only).
_jackd_alsa_xrun_stats() {
    local since="$1"
    local until="$2"
    if ! command -v journalctl >/dev/null 2>&1; then
        echo "0 0 0 0 0"
        return 0
    fi
    journalctl -u mpe-jackd.service --since "$since" --until "$until" --no-pager 2>/dev/null \
        | awk '
            /xrun of at least/ {
                if (match($0, /at least [0-9.]+ msecs/)) {
                    v = substr($0, RSTART + 9, RLENGTH - 15) + 0
                    n++
                    s += v
                    vals[n] = v
                    if (n == 1 || v < min) min = v
                    if (n == 1 || v > max) max = v
                }
            }
            END {
                if (n == 0) {
                    printf "0 0 0 0 0\n"
                    exit
                }
                for (i = 1; i <= n; i++) {
                    for (j = i + 1; j <= n; j++) {
                        if (vals[i] > vals[j]) { t = vals[i]; vals[i] = vals[j]; vals[j] = t }
                    }
                }
                med = vals[int((n + 1) / 2)]
                printf "%d %.6f %.6f %.6f %.6f\n", n, min, med, max, s / n
            }
        '
}

_restore_all() {
    _stop_midi_load
    _stop_fill_poller
    _stop_xrun_probe
    _restore_softmode
    if [ "$SKIP_BUFFER_RESTORE" = true ]; then
        return 0
    fi
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

_jack_periods_from_proc() {
    local pid
    pid="$(pgrep -x jackd | head -1)" || return 1
    tr '\0' '\n' < "/proc/$pid/cmdline" | awk '/^-n$/{getline; print; exit}'
}

_assert_jack_periods() {
    local want="$1" got
    got="$(_jack_periods_from_proc)" || {
        echo "ERROR: jackd not running — cannot verify period count" >&2
        return 1
    }
    if [ "$got" != "$want" ]; then
        echo "ERROR: JACK period count is $got, expected $want (trap 5 — env write may have failed)" >&2
        return 1
    fi
    echo "jackd periods=$got ok"
    return 0
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
        echo "ERROR: MPE_PEAK_METER is not 1 — xrun count requires meter.state (/etc/mpe/mpe.env)" >&2
        exit 1
    fi
    if ! systemctl is-active --quiet mpe-peak-meter.service 2>/dev/null; then
        if ! systemctl start mpe-peak-meter.service 2>/dev/null; then
            echo "ERROR: mpe-peak-meter.service failed to start" >&2
            exit 1
        fi
        sleep 2
    fi
    if ! mpe_meter_assert_live; then
        echo "ERROR: peak meter active but meter.state is not live" >&2
        exit 1
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
    _as_user taskset -c 2-3 "$PROBE_BIN" "$logpath" &
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

_meter_max_age_s=0

_meter_xruns() {
    local xr
    xr="$(mpe_meter_xruns_read)" || return 1
    if [ "${MPE_METER_LAST_AGE_S:-0}" -gt "$_meter_max_age_s" ]; then
        _meter_max_age_s="$MPE_METER_LAST_AGE_S"
    fi
    printf '%s\n' "$xr"
}

_run_window() {
    local tag="$1"
    local run_file="$2"
    local dsp_raw="$3"
    local xrun_events="$4"
    local fill_log="${5:-}"
    local status_file="${6:-}"
    local i dsp prev_xr start_xr cur_xr delta samples=0
    local window_since window_until
    local jackd_alsa_n jackd_alsa_min jackd_alsa_med jackd_alsa_max jackd_alsa_mean
    local probe_xrun_n
    local jitter_floor

    # shellcheck source=lib/measurement-result.sh
    source "$SCRIPT_DIR/lib/measurement-result.sh"

    _meter_max_age_s=0

    : >"$run_file"
    : >"$dsp_raw"
    rm -f "$xrun_events"

    local _window_align=0

    if ! _start_xrun_probe "$xrun_events"; then
        return 1
    fi

    local w=0
    while [ "$w" -lt 50 ]; do
        grep -q '^PROBE_ACTIVE' "$xrun_events" 2>/dev/null && break
        sleep 0.1
        w=$((w + 1))
    done
    if ! grep -q '^PROBE_ACTIVE' "$xrun_events" 2>/dev/null; then
        echo "ERROR: probe never signalled PROBE_ACTIVE — window VOID" >&2
        _stop_xrun_probe "$xrun_events"
        return 1
    fi
    _window_align=1

    if [ -n "$fill_log" ] && [ -n "$status_file" ]; then
        if ! _start_fill_poller "$fill_log" "$status_file" "$SECONDS_PER_RUN"; then
            _stop_xrun_probe "$xrun_events"
            return 1
        fi
    fi

    if ! start_xr="$(_meter_xruns)"; then
        _stop_fill_poller
        _stop_xrun_probe "$xrun_events"
        return 1
    fi
    prev_xr="$start_xr"
    window_since="$(date -Is)"

    _as_user stdbuf -oL jack_cpu_load >"$dsp_raw" 2>/dev/null &
    local jcl=$!
    _kill_jcl() { kill -9 "$jcl" 2>/dev/null || true; wait "$jcl" 2>/dev/null || true; }

    printf '  %4s %8s %8s %7s\n' "t" "dsp%" "xruns" "delta" >>"$run_file"

    for ((i = 1; i <= SECONDS_PER_RUN; i++)); do
        sleep 1
        samples=$((samples + 1))
        dsp="$(tail -1 "$dsp_raw" 2>/dev/null | grep -oP '[0-9]+\.[0-9]+' | head -1 || echo '?')"
        if ! cur_xr="$(_meter_xruns)"; then
            _kill_jcl
            _stop_fill_poller
            _stop_xrun_probe "$xrun_events"
            return 1
        fi
        if [ "$cur_xr" -lt "$prev_xr" ]; then
            echo "ERROR: meter restarted mid-run (xruns ${prev_xr} -> ${cur_xr}) — window VOID" >&2
            _kill_jcl
            _stop_fill_poller
            _stop_xrun_probe "$xrun_events"
            return 1
        fi
        delta=$((cur_xr - prev_xr))
        mark=""
        [ "$delta" -gt 0 ] && mark=" <<< XRUN x$delta"
        printf '  %4d %8s %8s %7d%s\n' "$i" "${dsp:-?}" "$cur_xr" "$delta" >>"$run_file"
        prev_xr="$cur_xr"
    done

    window_until="$(date -Is)"
    _kill_jcl
    _stop_fill_poller
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
                if (n==0) { exit 1 }
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
    ) || {
        echo "ERROR: no DSP samples in run file (dead jack_cpu_load path)" >&2
        return 1
    }

    local temp throttle
    read -r jitter_n jitter_med jitter_p99 jitter_p999 jitter_max < <(_delay_stats "$xrun_events")
    read -r late_p99 late_max < <(_frames_late_stats "$xrun_events")
    read -r delay_n delay_nz delay_med delay_p99 delay_max < <(_delay_stats_legacy "$xrun_events")

    if [ "$SECONDS_PER_RUN" -lt 1 ]; then
        echo "ERROR: SECONDS_PER_RUN must be >= 1" >&2
        return 1
    fi
    jitter_floor="$(mpe_result_jitter_n_floor "$SECONDS_PER_RUN")" || return 1
    if [ "$jitter_n" -lt "$jitter_floor" ]; then
        echo "ERROR: jitter_n=${jitter_n} below floor ${jitter_floor} for ${SECONDS_PER_RUN}s window — probe process callback produced too few samples" >&2
        return 1
    fi
    temp="$(vcgencmd measure_temp 2>/dev/null || echo 'temp=unknown')"
    throttle="$(vcgencmd get_throttled 2>/dev/null || echo 'throttled=unknown')"

    if ! mpe_meter_assert_live; then
        echo "ERROR: meter.state not live at end of window ${tag}" >&2
        return 1
    fi

    read -r jackd_alsa_n jackd_alsa_min jackd_alsa_med jackd_alsa_max jackd_alsa_mean < <(
        _jackd_alsa_xrun_stats "$window_since" "$window_until"
    )
    probe_xrun_n="$(grep '^XRUN_COUNT ' "$xrun_events" 2>/dev/null | awk '{print $2}' || echo 0)"

    MPE_R_xruns=$total_xr
    MPE_R_dsp_median=$dsp_median
    MPE_R_samples=$samples
    MPE_R_jitter_n=$jitter_n
    MPE_R_tag=$tag
    MPE_EXPECT_SAMPLES=$SECONDS_PER_RUN
    export MPE_EXPECT_SAMPLES
    if ! mpe_result_physics_assert "$BUFFER"; then
        echo "ERROR: physics assertion failed for ${tag}" >&2
        return 1
    fi

    {
        echo "RESULT tag=${tag} xruns=${total_xr} meter_live=1 meter_max_age_s=${_meter_max_age_s} dsp_median=${dsp_median} dsp_p99=${dsp_p99} dsp_max=${dsp_max} window_align=${_window_align}"
        echo "RESULT tag=${tag} jitter_n=${jitter_n} jitter_median_usec=${jitter_med} jitter_p99_usec=${jitter_p99} jitter_p99_9_usec=${jitter_p999} jitter_max_usec=${jitter_max}"
        echo "RESULT tag=${tag} frames_late_p99_usec=${late_p99} frames_late_max_usec=${late_max}"
        echo "RESULT tag=${tag} delay_events=${delay_n} delay_nonzero=${delay_nz} (legacy, ignore)"
        echo "RESULT tag=${tag} samples=${samples} ${temp} ${throttle}"
        echo "RESULT tag=${tag} file=${run_file} xrun_events=${xrun_events}"
        echo "RESULT tag=${tag} probe_xrun_count=${probe_xrun_n} jackd_alsa_xrun_count=${jackd_alsa_n} jackd_alsa_msec_min=${jackd_alsa_min} jackd_alsa_msec_median=${jackd_alsa_med} jackd_alsa_msec_max=${jackd_alsa_max} jackd_alsa_msec_mean=${jackd_alsa_mean} window_since=${window_since} window_until=${window_until}"
        if [ -n "$fill_log" ]; then
            echo "RESULT tag=${tag} fill_log=${fill_log}"
        fi
    }

    echo "SENTINEL run-complete tag=${tag} xruns=${total_xr}"
    return 0
}

{
    echo
    echo "=== measure-latency-run buffer=${BUFFER} periods=${PERIODS:-$(mpe_jack_periods 2>/dev/null || echo ?)} condition=${CONDITION} runs=${RUNS} seconds=${SECONDS_PER_RUN} $(date -Is) ==="
    echo "SENTINEL harness-start"
    _ensure_peak_meter
    _ensure_xrun_probe || exit 1

    _audio_args=( )
    [ -n "$BUFFER" ] && _audio_args+=(--buffer "$BUFFER")
    [ -n "$PERIODS" ] && _audio_args+=(--periods "$PERIODS")
    if [ "${#_audio_args[@]}" -eq 0 ]; then
        echo "ERROR: --buffer is required" >&2
        exit 2
    fi
    if ! "$SCRIPT_DIR/set-surge-audio.sh" "${_audio_args[@]}"; then
        echo "ERROR: set-surge-audio.sh ${_audio_args[*]} failed" >&2
        exit 1
    fi
    mpe_source_appliance_env
    PERIODS_EFFECTIVE="$(mpe_jack_periods)"
    sleep 8
    _assert_jack_period "$BUFFER" || exit 1
    _assert_jack_periods "$PERIODS_EFFECTIVE" || exit 1

    if ! _enable_strict_xrun_reporting; then
        echo "ERROR: could not restart jackd for strict xrun reporting" >&2
        exit 1
    fi
    _assert_jack_period "$BUFFER" || exit 1
    _assert_jack_periods "$PERIODS_EFFECTIVE" || exit 1
    echo "=== provenance after strict restart ==="
    _record_provenance

    if [ -n "$PROVENANCE_PATCH" ] || [ "$HOLD_VOICES" -gt 0 ]; then
        {
            echo "PROVENANCE patch=${PROVENANCE_PATCH:-unknown} hold_voices=${PROVENANCE_VOICES:-$HOLD_VOICES} buffer=${BUFFER} periods=${PERIODS_EFFECTIVE} condition=${CONDITION} $(date -Is)"
        } >>"$OUTPUT"
    fi

    ALSA_STATUS=""
    if [ -n "$FILL_LOG" ]; then
        if ! RESOLVE="$("$SCRIPT_DIR/resolve-alsa-playback-status.sh")"; then
            echo "ERROR: resolve-alsa-playback-status failed (fill-log requested)" >&2
            exit 1
        fi
        ALSA_STATUS="$(printf '%s\n' "$RESOLVE" | awk -F= '/^STATUS=/{print $2}')"
        echo "=== fill telemetry card $(printf '%s\n' "$RESOLVE" | awk -F= '/^CARD=/{print $2}') status=${ALSA_STATUS} ==="
    fi

    if [ "$PLAYING_LOOPS" -gt 0 ]; then
        case "$PLAYING_LOOPS" in
            4 | 8 | 16) ;;
            *)
                echo "ERROR: --playing-loops must be 0, 4, 8, or 16" >&2
                exit 2
                ;;
        esac
        if ! grep -q "^MPE_SL_LOOPS=" "$ENV_FILE" 2>/dev/null; then
            printf '\nMPE_SL_LOOPS=%s\n' "$PLAYING_LOOPS" >>"$ENV_FILE"
        else
            sed -i "s/^MPE_SL_LOOPS=.*/MPE_SL_LOOPS=${PLAYING_LOOPS}/" "$ENV_FILE"
        fi
        systemctl restart mpe-sooperlooper.service
        sleep 8
        if ! bash "${SCRIPT_DIR}/sooperlooper/load-n-loops.sh" "$PLAYING_LOOPS"; then
            echo "ERROR: load-n-loops ${PLAYING_LOOPS} failed" >&2
            exit 1
        fi
        echo "=== playing-loops=${PLAYING_LOOPS} loaded ==="
    fi

    run_idx=1
    while [ "$run_idx" -le "$RUNS" ]; do
        # Tag carries buffer and loop count, not just condition. Two blocks that
        # share a condition -- e.g. the 1024 loop curve, where loops8 and loops16
        # are both condition B -- previously both emitted B-run1..15 into one
        # append-only log. Any analysis that deduped or grepped by tag would have
        # silently merged them (2026-08-20; the split was done by block marker
        # instead, so no result was corrupted -- but nothing prevented it).
        tag="${CONDITION}-b${BUFFER}-p${PERIODS_EFFECTIVE}-l${PLAYING_LOOPS}-run${run_idx}"

        # Belt and braces: whatever the cause, a repeated tag in one output file
        # makes that file ambiguous. Fail loudly rather than append a second run
        # under a name that already means something else.
        if [ -f "$OUTPUT" ] && grep -q "^RESULT tag=${tag} xruns=" "$OUTPUT"; then
            echo "ERROR: tag ${tag} already present in ${OUTPUT} — refusing to append" >&2
            echo "       a duplicate tag makes the log ambiguous; use a fresh --output" >&2
            exit 1
        fi
        echo "=== run ${tag} ==="
        stamp="$(date +%s)"
        run_file="/tmp/latency-${tag}-${stamp}.out"
        dsp_raw="/tmp/latency-${tag}-${stamp}.dsp"
        xev="/tmp/latency-${tag}-${stamp}.xruns"
        fill_file=""
        if [ -n "$FILL_LOG" ]; then
            fill_file="${FILL_LOG}-${tag}.log"
        fi

        if [ "$HOLD_VOICES" -gt 0 ]; then
            _as_user python3 "$SCRIPT_DIR/midi-load-hold.py" "$((SECONDS_PER_RUN + 5))" "$HOLD_VOICES" \
                >"/tmp/latency-midi-load-${stamp}.log" 2>&1 &
            _LOAD_PID=$!
            sleep 2
        else
            _as_user python3 "$SCRIPT_DIR/midi-load.py" "$((SECONDS_PER_RUN + 20))" \
                >"/tmp/latency-midi-load-${stamp}.log" 2>&1 &
            _LOAD_PID=$!
            sleep 8
        fi

        if ! _run_window "$tag" "$run_file" "$dsp_raw" "$xev" "$fill_file" "$ALSA_STATUS"; then
            _stop_midi_load
            exit 1
        fi
        _stop_midi_load

        if [ -n "$fill_file" ] && [ -s "$fill_file" ]; then
            if ! "$SCRIPT_DIR/summarize-fill-trace.sh" "$fill_file" "$BUFFER" "$PERIODS_EFFECTIVE" >>"$OUTPUT"; then
                echo "ERROR: fill trace sanity check failed for ${fill_file}" >&2
                exit 1
            fi
        fi

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

    echo "=== restore buffer ${RESTORE_BUFFER} periods ${RESTORE_PERIODS} ==="
    if [ "$SKIP_BUFFER_RESTORE" != true ]; then
        "$SCRIPT_DIR/set-surge-audio.sh" --buffer "$RESTORE_BUFFER" --periods "$RESTORE_PERIODS" || true
        sleep 6
        _assert_jack_period "$RESTORE_BUFFER" || echo "WARNING: restore period check failed" >&2
        _assert_jack_periods "$RESTORE_PERIODS" || echo "WARNING: restore periods check failed" >&2
    else
        echo "skip restore (--no-restore-buffer)"
    fi
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
