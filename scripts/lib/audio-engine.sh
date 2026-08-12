#!/bin/bash
# Audio engine (jack | alsa) — shared resolution, JACK server probes, runtime state.
#
# Engine selection: MPE_AUDIO_ENGINE in /etc/mpe/mpe.env. Default is "jack"
# (spec Documents/specs/jack-audio-engine-spec.md criterion 12). "alsa" is the
# full-fidelity regression path — Surge opens the tier device directly, exactly
# as it did before the graph server existed.
#
# Runtime state lives in /run/mpe (tmpfs). It must NOT live in a shell variable:
# surge-watchdog.service is BindsTo=surge-xt-cli.service, so the supervisor is
# itself restarted every time it restarts Surge — in-memory cooldown state would
# be wiped on exactly the event it rate-limits (spec D3).

MPE_AUDIO_ENGINE_DEFAULT=jack

# Server-side period, distinct from MPE_SURGE_BUFFER_SIZE (spec D6): under JACK
# the period belongs to the server, not to Surge. Proven-good on the Sound
# Blaster Play! 3: 256 frames x 3 periods @ 48 kHz, S24_3LE, zero xruns.
MPE_JACK_BUFFER_DEFAULT=256
MPE_JACK_PERIODS_DEFAULT=3
MPE_JACK_RATE_DEFAULT=48000

# jackd's own audio thread priority. Measured live: jackd 70, Surge client 65.
MPE_JACK_RT_PRIORITY_DEFAULT=70

# Bounded readiness wait for the server before Surge falls back to ALSA.
MPE_JACK_READY_TIMEOUT_DEFAULT=10

MPE_JACKD_SERVICE="mpe-jackd.service"
MPE_SURGE_SERVICE_UNIT="surge-xt-cli.service"

mpe_audio_engine() {
    case "${MPE_AUDIO_ENGINE:-}" in
        alsa | ALSA) printf 'alsa' ;;
        jack | JACK) printf 'jack' ;;
        '') printf '%s' "$MPE_AUDIO_ENGINE_DEFAULT" ;;
        *)
            # Never leave the instrument silent over a typo — fall back to the
            # default engine and say so.
            echo "WARNING: MPE_AUDIO_ENGINE='${MPE_AUDIO_ENGINE}' unrecognised — using $MPE_AUDIO_ENGINE_DEFAULT" >&2
            printf '%s' "$MPE_AUDIO_ENGINE_DEFAULT"
            ;;
    esac
}

mpe_engine_is_jack() {
    [ "$(mpe_audio_engine)" = jack ]
}

mpe_jack_period() {
    case "${MPE_JACK_BUFFER:-}" in
        32 | 64 | 128 | 256 | 512 | 768 | 1024 | 2048) printf '%s' "$MPE_JACK_BUFFER" ;;
        '') printf '%s' "$MPE_JACK_BUFFER_DEFAULT" ;;
        *)
            echo "WARNING: MPE_JACK_BUFFER='${MPE_JACK_BUFFER}' invalid — using $MPE_JACK_BUFFER_DEFAULT" >&2
            printf '%s' "$MPE_JACK_BUFFER_DEFAULT"
            ;;
    esac
}

mpe_jack_periods() {
    case "${MPE_JACK_PERIODS:-}" in
        2 | 3 | 4) printf '%s' "$MPE_JACK_PERIODS" ;;
        '') printf '%s' "$MPE_JACK_PERIODS_DEFAULT" ;;
        *)
            echo "WARNING: MPE_JACK_PERIODS='${MPE_JACK_PERIODS}' invalid — using $MPE_JACK_PERIODS_DEFAULT" >&2
            printf '%s' "$MPE_JACK_PERIODS_DEFAULT"
            ;;
    esac
}

# Sample rate stays a single appliance-wide setting (MPE_SURGE_SAMPLE_RATE) so
# the UAC2 gadget and the graph cannot disagree. Only the period keys split.
mpe_jack_rate() {
    case "${MPE_SURGE_SAMPLE_RATE:-}" in
        44100 | 48000 | 96000) printf '%s' "$MPE_SURGE_SAMPLE_RATE" ;;
        *) printf '%s' "$MPE_JACK_RATE_DEFAULT" ;;
    esac
}

mpe_jack_rt_priority() {
    case "${MPE_JACK_RT_PRIORITY:-}" in
        '' | *[!0-9]*) printf '%s' "$MPE_JACK_RT_PRIORITY_DEFAULT" ;;
        *) printf '%s' "$MPE_JACK_RT_PRIORITY" ;;
    esac
}

mpe_jack_ready_timeout() {
    case "${MPE_JACK_READY_TIMEOUT_S:-}" in
        '' | *[!0-9]*) printf '%s' "$MPE_JACK_READY_TIMEOUT_DEFAULT" ;;
        *) printf '%s' "$MPE_JACK_READY_TIMEOUT_S" ;;
    esac
}

# ---------------------------------------------------------------------------
# Runtime state directory
# ---------------------------------------------------------------------------

# /run is root-owned; the units that write here declare RuntimeDirectory=mpe so
# systemd creates it owned by the appliance user. Fall back to a per-user tmp
# dir rather than losing state entirely when run by hand.
mpe_run_dir() {
    local dir="${MPE_RUN_DIR:-/run/mpe}"
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir" 2>/dev/null || true
    fi
    if [ ! -w "$dir" ]; then
        echo "WARNING: $dir not writable — falling back to ${TMPDIR:-/tmp}/mpe (state may split across processes)" >&2
        dir="${TMPDIR:-/tmp}/mpe"
        mkdir -p "$dir" 2>/dev/null || true
    fi
    printf '%s' "$dir"
}

# Atomic KEY=value file write — three writers touch engine.state.
mpe_state_write_atomic() {
    local file="${1:?file required}"
    shift
    local tmp="${file}.tmp.$$"
    {
        while [ "$#" -gt 0 ]; do
            printf '%s\n' "$1"
            shift
        done
    } >"$tmp" 2>/dev/null || return 0
    chmod 0644 "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$file" 2>/dev/null || rm -f "$tmp" 2>/dev/null || true
}

mpe_engine_state_file() {
    printf '%s' "${MPE_ENGINE_STATE_FILE:-$(mpe_run_dir)/engine.state}"
}

mpe_jack_state_file() {
    printf '%s' "${MPE_JACK_STATE_FILE:-$(mpe_run_dir)/jack.state}"
}

mpe_engine_reconcile_file() {
    printf '%s' "${MPE_ENGINE_RECONCILE_STATE:-$(mpe_run_dir)/engine-reconcile.state}"
}

# Written only by start-surge-cli.sh, so the supervisor can tell which engine
# Surge actually landed on and when — kept separate from the published status
# file, which both writers rewrite whole.
mpe_surge_state_file() {
    printf '%s' "${MPE_SURGE_STATE_FILE:-$(mpe_run_dir)/surge.state}"
}

# Read one key from a KEY=value state file. Empty output when absent.
mpe_state_get() {
    local file="${1:?state file required}"
    local key="${2:?key required}"
    [ -r "$file" ] || return 0
    grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2-
}

# Publish engine state for `mpe engine status` and the touch HUD.
#   engine=  requested engine (jack|alsa)
#   active=  engine Surge actually started on (jack|alsa|none)
#   state=   ok | degraded | recovering | failed  (spec D3)
#   reason=  short machine-readable cause, e.g. no-server, no-alsa-device
#   looper=  guarded | enabled | off
mpe_engine_state_write() {
    local engine="${1:?engine required}"
    local active="${2:?active engine required}"
    local state="${3:?state required}"
    local reason="${4:-}"
    local looper="${5:-off}"
    local file
    file="$(mpe_engine_state_file)"
    mpe_state_write_atomic "$file" \
        "engine=$engine" \
        "active=$active" \
        "state=$state" \
        "reason=$reason" \
        "looper=$looper" \
        "updated=$(date +%s)"
}

mpe_engine_state_get() {
    mpe_state_get "$(mpe_engine_state_file)" "${1:?key required}"
}

# Looper state for the engine status line: guarded whenever the looper is asked
# for while the engine is JACK (spec D5).
mpe_looper_state_label() {
    if [ "${MPE_LOOPER_ENABLED:-0}" != "1" ]; then
        printf 'off'
    elif mpe_engine_is_jack; then
        printf 'guarded'
    else
        printf 'enabled'
    fi
}

# ---------------------------------------------------------------------------
# JACK server probes
# ---------------------------------------------------------------------------

mpe_jack_server_running() {
    pgrep -x jackd >/dev/null 2>&1
}

# Running is not the same as accepting clients. jack_lsp is a hard prerequisite
# (spec assumptions: jack-example-tools installed). Both server-ready and graph
# probes must agree — missing jack_lsp is not "server up".
mpe_jack_server_ready() {
    local quiet="${1:-0}"
    mpe_jack_server_running || return 1
    if ! command -v jack_lsp >/dev/null 2>&1; then
        [ "$quiet" = 1 ] || echo "ERROR: jack_lsp not found — install jack-example-tools" >&2
        return 1
    fi
    timeout 3 jack_lsp >/dev/null 2>&1
}

# Bounded readiness wait — never a fixed sleep (spec D3 boot ordering).
mpe_wait_for_jack_server() {
    local timeout="${1:-$(mpe_jack_ready_timeout)}"
    local waited=0
    local step_ms=250
    if mpe_jack_server_running && ! command -v jack_lsp >/dev/null 2>&1; then
        echo "ERROR: jack_lsp not found — install jack-example-tools" >&2
        return 1
    fi
    while :; do
        if mpe_jack_server_ready 1; then
            return 0
        fi
        if [ "$waited" -ge "$((timeout * 1000))" ]; then
            return 1
        fi
        sleep 0.25
        waited=$((waited + step_ms))
    done
}

# Stop jackd before Surge opens the ALSA tier device (spec D3 degraded fallback).
# Non-blocking stop is required: a blocking stop from inside surge-xt-cli's
# ExecStart, with After=mpe-jackd.service, can deadlock systemd job ordering.
MPE_JACK_RELEASE_TIMEOUT_DEFAULT=5

mpe_jack_release_timeout() {
    case "${MPE_JACK_RELEASE_TIMEOUT_S:-}" in
        '' | *[!0-9]*) printf '%s' "$MPE_JACK_RELEASE_TIMEOUT_DEFAULT" ;;
        *) printf '%s' "$MPE_JACK_RELEASE_TIMEOUT_S" ;;
    esac
}

mpe_release_audio_device_for_alsa() {
    mpe_systemctl stop --no-block "$MPE_JACKD_SERVICE" || return 1
    local timeout waited step_ms
    timeout="$(mpe_jack_release_timeout)"
    waited=0
    step_ms=250
    while mpe_jack_server_running; do
        if [ "$waited" -ge "$((timeout * 1000))" ]; then
            return 1
        fi
        sleep 0.25
        waited=$((waited + step_ms))
    done
    return 0
}

mpe_jack_state_write() {
    local device="${1:-}"
    local period="${2:-}"
    local periods="${3:-}"
    local rate="${4:-}"
    local file
    file="$(mpe_jack_state_file)"
    mpe_state_write_atomic "$file" \
        "started=$(date +%s)" \
        "device=$device" \
        "period=$period" \
        "periods=$periods" \
        "rate=$rate"
}

# Epoch seconds when jackd last started, or 0 when unknown. Written by
# start-jackd.sh rather than parsed out of systemctl so it is testable and works
# for a hand-started server too.
mpe_jack_start_epoch() {
    local value
    value="$(mpe_state_get "$(mpe_jack_state_file)" started)"
    case "$value" in
        '' | *[!0-9]*) printf '0' ;;
        *) printf '%s' "$value" ;;
    esac
}

mpe_surge_state_write() {
    local active="${1:?active engine required}"
    local device="${2:-}"
    local file
    file="$(mpe_surge_state_file)"
    mpe_state_write_atomic "$file" \
        "started=$(date +%s)" \
        "active=$active" \
        "device=$device"
}

# Which engine Surge is currently running on: jack | alsa | unknown.
mpe_surge_active_engine() {
    local value
    value="$(mpe_state_get "$(mpe_surge_state_file)" active)"
    case "$value" in
        jack | alsa) printf '%s' "$value" ;;
        *) printf 'unknown' ;;
    esac
}

# ---------------------------------------------------------------------------
# Device changes restart the graph, not Surge (spec D2)
# ---------------------------------------------------------------------------

# jackd binds one device at start, so anything that can change the device must
# restart jackd. Surge is reconciled onto the new server by surge-watchdog.sh —
# restarting jackd deliberately does not restart Surge (that decoupling is what
# makes the boot fallback and the promotion case reachable).
mpe_audio_graph_unit() {
    if mpe_engine_is_jack; then
        printf '%s' "$MPE_JACKD_SERVICE"
    else
        printf '%s' "$MPE_SURGE_SERVICE_UNIT"
    fi
}

mpe_systemctl() {
    if [ "$(id -u)" -eq 0 ]; then
        systemctl "$@"
        return $?
    fi
    sudo -n systemctl "$@"
}

mpe_restart_audio_graph() {
    local unit
    # Surge holds the tier device on ALSA after a jack→ALSA fallback; restarting
    # jackd would EBUSY forever (Restart=always) without improving sound.
    if mpe_engine_is_jack && [ "$(mpe_surge_active_engine)" = alsa ]; then
        echo "restart-audio-graph: skipping mpe-jackd restart — Surge holds ALSA device (degraded)" >&2
        return 0
    fi
    unit="$(mpe_audio_graph_unit)"
    # A unit sitting in start-limit failure refuses `restart` ("start request
    # repeated too quickly") until it is reset. That is exactly the state a DAC
    # unplug leaves jackd in, and the replug is the event that must recover it.
    mpe_systemctl reset-failed "$unit" >/dev/null 2>&1 || true
    if ! mpe_systemctl restart --no-block "$unit" 2>/dev/null; then
        echo "WARNING: could not restart $unit (no root / no passwordless sudo)" >&2
        return 1
    fi
    # A device change or an operator restart is a new situation, so the supervisor
    # gets its restart budget back — otherwise an appliance that reached
    # state=failed stays there through the very action meant to fix it.
    mpe_engine_reconcile_reset
    return 0
}

# ---------------------------------------------------------------------------
# Supervisor cooldown (spec D3)
# ---------------------------------------------------------------------------
#
# The watchdog polls every 5 s while Surge has RestartSec=10, StartLimitBurst=5,
# StartLimitIntervalSec=300. An uncooled supervisor exhausts the burst budget in
# ~25 s and leaves Surge dead until manual intervention — worse than the fault it
# responds to. Hence: first restart immediate, then >= 90 s apart, never while
# jackd is still settling, and escalate to state=failed after 3 tries.

MPE_ENGINE_COOLDOWN_DEFAULT=90
MPE_ENGINE_JACKD_SETTLE_DEFAULT=15
MPE_ENGINE_MAX_RESTARTS_DEFAULT=3

mpe_engine_cooldown_seconds() {
    case "${MPE_ENGINE_COOLDOWN_S:-}" in
        '' | *[!0-9]*) printf '%s' "$MPE_ENGINE_COOLDOWN_DEFAULT" ;;
        *) printf '%s' "$MPE_ENGINE_COOLDOWN_S" ;;
    esac
}

mpe_engine_jackd_settle_seconds() {
    case "${MPE_ENGINE_JACKD_SETTLE_S:-}" in
        '' | *[!0-9]*) printf '%s' "$MPE_ENGINE_JACKD_SETTLE_DEFAULT" ;;
        *) printf '%s' "$MPE_ENGINE_JACKD_SETTLE_S" ;;
    esac
}

mpe_engine_max_restarts() {
    case "${MPE_ENGINE_MAX_RESTARTS:-}" in
        '' | *[!0-9]*) printf '%s' "$MPE_ENGINE_MAX_RESTARTS_DEFAULT" ;;
        *) printf '%s' "$MPE_ENGINE_MAX_RESTARTS" ;;
    esac
}

# mpe_engine_reconcile_decision <now> <last_restart_epoch> <restart_count> <jackd_start_epoch>
#
# Pure decision function — no I/O, so it is unit testable (tests/test_audio_engine.py).
# Prints exactly one of: restart | jackd-settling | cooldown | failed
mpe_engine_reconcile_decision() {
    local now="${1:?now required}"
    local last="${2:-0}"
    local count="${3:-0}"
    local jackd_start="${4:-0}"

    if [ "$count" -ge "$(mpe_engine_max_restarts)" ]; then
        printf 'failed'
        return 0
    fi
    if [ "$jackd_start" -gt 0 ] && [ "$((now - jackd_start))" -lt "$(mpe_engine_jackd_settle_seconds)" ]; then
        printf 'jackd-settling'
        return 0
    fi
    if [ "$last" -le 0 ]; then
        printf 'restart'
        return 0
    fi
    if [ "$((now - last))" -ge "$(mpe_engine_cooldown_seconds)" ]; then
        printf 'restart'
    else
        printf 'cooldown'
    fi
}

mpe_engine_reconcile_last_restart() {
    local value
    value="$(mpe_state_get "$(mpe_engine_reconcile_file)" last_restart)"
    case "$value" in
        '' | *[!0-9]*) printf '0' ;;
        *) printf '%s' "$value" ;;
    esac
}

mpe_engine_reconcile_count() {
    local value
    value="$(mpe_state_get "$(mpe_engine_reconcile_file)" restarts)"
    case "$value" in
        '' | *[!0-9]*) printf '0' ;;
        *) printf '%s' "$value" ;;
    esac
}

mpe_engine_reconcile_record_restart() {
    local file count
    file="$(mpe_engine_reconcile_file)"
    count="$(($(mpe_engine_reconcile_count) + 1))"
    mpe_state_write_atomic "$file" \
        "last_restart=$(date +%s)" \
        "restarts=$count"
}

# Called once the engine is observed healthy — the restart budget only exists to
# stop an unbounded loop, so reaching ok must clear it.
mpe_engine_reconcile_reset() {
    rm -f "$(mpe_engine_reconcile_file)" 2>/dev/null || true
}

mpe_surge_on_jack_graph() {
    mpe_jack_server_ready || return 1
    if ! command -v jack_lsp >/dev/null 2>&1; then
        return 1
    fi
    jack_lsp 2>/dev/null | grep -qi 'surge'
}

# Back-compat alias used by udev helper and profile scripts.
restart_audio_graph() {
    mpe_restart_audio_graph
}
