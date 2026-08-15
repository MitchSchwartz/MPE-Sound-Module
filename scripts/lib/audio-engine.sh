#!/bin/bash
# JACK audio engine — shared resolution, server probes, runtime state.
#
# JACK is the only audio engine (spec D3 amended 2026-08-13 — ALSA removed
# entirely as a product audio path, not just its automatic fallback). There is
# no MPE_AUDIO_ENGINE to select: Surge is always a JACK client, and a jackd
# that will not start is a hard failure, not a route to an alternate engine.
#
# Runtime state lives in /run/mpe (tmpfs). It must NOT live in a shell variable:
# the watchdog restarts Surge and can itself be restarted (Restart=always) or
# re-run across Surge restarts — in-memory cooldown state would be wiped on
# exactly the events it rate-limits (spec D3).

# Published in engine.state's `engine=` field as a static, non-configurable
# constant — nothing reads MPE_AUDIO_ENGINE anymore, but `mpe engine status`
# and the touch HUD still parse an `engine=` key, so the key stays and its
# value stays literally "jack" rather than dropping the key outright.
MPE_ENGINE_NAME="jack"

# Server-side period, distinct from MPE_SURGE_BUFFER_SIZE (spec D6): under JACK
# the period belongs to the server, not to Surge. Proven-good on the Sound
# Blaster Play! 3: 256 frames x 3 periods @ 48 kHz, S24_3LE, zero xruns.
MPE_JACK_BUFFER_DEFAULT=256
MPE_JACK_PERIODS_DEFAULT=3
MPE_JACK_RATE_DEFAULT=48000

# jackd's own audio thread priority. Measured live: jackd 70, Surge client 65.
MPE_JACK_RT_PRIORITY_DEFAULT=70

# Bounded readiness wait for the server before Surge gives up and fails loud
# (no ALSA to fall back to — see start-surge-cli.sh).
MPE_JACK_READY_TIMEOUT_DEFAULT=10

MPE_JACKD_SERVICE="mpe-jackd.service"

mpe_jack_period() {
    case "${MPE_JACK_BUFFER:-}" in
        64 | 128 | 256 | 512 | 1024) printf '%s' "$MPE_JACK_BUFFER" ;;
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
    } >"$tmp" 2>/dev/null || {
        echo "WARNING: failed to write state temp file for $file (disk full or unwritable?)" >&2
        rm -f "$tmp" 2>/dev/null || true
        return 1
    }
    chmod 0644 "$tmp" 2>/dev/null || true
    mv -f "$tmp" "$file" 2>/dev/null || {
        echo "WARNING: failed to install state file $file" >&2
        rm -f "$tmp" 2>/dev/null || true
        return 1
    }
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
#   engine=  always "jack" (MPE_ENGINE_NAME) — kept as a key for downstream
#            parsers even though there is nothing left to select (spec D3
#            amended 2026-08-13 — ALSA removed entirely, not just its fallback)
#   active=  jack | none — whether Surge actually landed on the graph
#   state=   ok | recovering | failed  (spec D3; `degraded` retired — there is
#            no lesser engine to rest on anymore)
#   reason=  short machine-readable cause, e.g. no-server, no-jack-device
#   looper=  guarded | enabled | off
mpe_engine_state_write() {
    local engine="${1:?engine required}"
    # Not ${2:?}: an unreadable or split state file makes callers pass an empty
    # active engine, and ${2:?} exits the *calling* shell — which killed
    # surge-watchdog.sh before it could restart a crashed Surge, leaving the
    # instrument unsupervised. A status publisher must never kill its caller.
    local active="${2:-unknown}"
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
        "updated=$(date +%s)" || true
}

mpe_engine_state_get() {
    mpe_state_get "$(mpe_engine_state_file)" "${1:?key required}"
}

# Looper state for the engine status line: guarded whenever the looper is asked
# for (spec D5) — JACK is the only engine, so this is no longer conditional on
# which engine is active. "enabled" remains a valid future label for when the
# Phase 2 callback client lifts the guard; nothing produces it yet.
mpe_looper_state_label() {
    if [ "${MPE_LOOPER_ENABLED:-0}" != "1" ]; then
        printf 'off'
    else
        printf 'guarded'
    fi
}

# ---------------------------------------------------------------------------
# JACK server probes
# ---------------------------------------------------------------------------

mpe_jack_server_running() {
    pgrep -x jackd >/dev/null 2>&1
}

# jackd runs as MPE_PI_USER (systemd User=). set-surge-audio.sh is invoked via
# sudo for /etc/mpe writes, so graph probes as root cannot see the user's JACK
# session — run jack_lsp as the graph owner instead.
mpe_jack_graph_user() {
    if [ -n "${MPE_PI_USER:-}" ]; then
        printf '%s' "$MPE_PI_USER"
        return 0
    fi
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
        printf '%s' "$SUDO_USER"
        return 0
    fi
    if [ -f /etc/mpe/mpe.env ]; then
        local u
        u="$(grep -E '^MPE_PI_USER=' /etc/mpe/mpe.env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
        if [ -n "$u" ]; then
            printf '%s' "$u"
            return 0
        fi
    fi
    id -un 2>/dev/null || printf '%s' "${USER:-root}"
}

mpe_jack_lsp() {
    local timeout_s="${MPE_JACK_LSP_TIMEOUT_S:-3}"
    if ! command -v jack_lsp >/dev/null 2>&1; then
        return 127
    fi
    if [ "$(id -u)" -eq 0 ]; then
        local owner
        owner="$(mpe_jack_graph_user)"
        if [ -n "$owner" ] && [ "$owner" != root ]; then
            timeout "$timeout_s" sudo -u "$owner" -E jack_lsp "$@"
            return $?
        fi
    fi
    timeout "$timeout_s" jack_lsp "$@"
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
    mpe_jack_lsp >/dev/null 2>&1
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
        "rate=$rate" || true
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
        "device=$device" || true
}

# ---------------------------------------------------------------------------
# Device changes restart the graph, not Surge (spec D2)
# ---------------------------------------------------------------------------

# jackd binds one device at start, so anything that can change the device must
# restart jackd. Surge is reconciled onto the new server by surge-watchdog.sh —
# restarting jackd deliberately does not restart Surge (that decoupling is what
# makes the boot-failure and the promotion case reachable). Single-engine: this
# is always mpe-jackd.service now — kept as a function (not inlined at call
# sites) purely so restart-audio-graph.sh and set-audio-profile.sh have one
# named seam to read rather than a bare constant.
mpe_audio_graph_unit() {
    printf '%s' "$MPE_JACKD_SERVICE"
}

mpe_systemctl() {
    if [ "$(id -u)" -eq 0 ]; then
        systemctl "$@"
        return $?
    fi
    sudo -n systemctl "$@"
}

# Cards whose bind/unbind must not restart the production graph (spec D2).
# udev remove events cannot match ATTR{id}; restart-audio-graph.sh receives
# %E{SOUND_CARD_ID} from the udev database instead (see 99-usb-audio.rules).
mpe_should_skip_graph_restart_for_card() {
    local card_id="${1:-}"
    case "$card_id" in
        vc4hdmi* | UAC2Gadget | UAC2 | Loopback) return 0 ;;
        *) return 1 ;;
    esac
}

# Criterion 2* failure path — publish state and exit (start-surge-cli.sh).
mpe_publish_jack_engine_failure() {
    local reason="${1:?reason required}"
    mpe_engine_state_write "$MPE_ENGINE_NAME" none failed "$reason" "$(mpe_looper_state_label)"
    mpe_surge_state_write none ""
}

mpe_restart_audio_graph() {
    local unit
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
# Planned operator graph changes (touch UI settings / profile switch)
# ---------------------------------------------------------------------------

mpe_planned_promote_flag_file() {
    printf '%s' "${MPE_PLANNED_PROMOTE_FLAG:-$(mpe_run_dir)/planned-promote}"
}

mpe_planned_promote_flag_set() {
    [ -f "$(mpe_planned_promote_flag_file)" ]
}

mpe_planned_promote_flag_mark() {
    local file
    file="$(mpe_planned_promote_flag_file)"
    date +%s >"$file" 2>/dev/null || true
    chmod 0644 "$file" 2>/dev/null || true
}

mpe_planned_promote_flag_clear() {
    rm -f "$(mpe_planned_promote_flag_file)" 2>/dev/null || true
}

mpe_wait_for_surge_on_graph() {
    local timeout="${1:-30}"
    local waited=0
    local step_ms=250
    while :; do
        if mpe_surge_on_jack_graph; then
            return 0
        fi
        if [ "$waited" -ge "$((timeout * 1000))" ]; then
            return 1
        fi
        sleep 0.25
        waited=$((waited + step_ms))
    done
}

# Operator-initiated device/buffer/rate/profile changes: restart jackd, sync-wait
# for the server, promote Surge once, and return only when the graph is ok. The
# passive watchdog path (settle + 5 s poll) is for unplanned recovery only.
mpe_promote_surge_planned() {
    local reason="${1:-planned-graph-change}"
    local timeout="${MPE_PLANNED_PROMOTE_TIMEOUT:-30}"
    local looper_label surge_service
    looper_label="$(mpe_looper_state_label)"
    surge_service="surge-xt-cli.service"

    mpe_planned_promote_flag_mark
    mpe_engine_state_write "$MPE_ENGINE_NAME" none recovering "$reason" "$looper_label"

    if ! mpe_restart_audio_graph; then
        mpe_planned_promote_flag_clear
        mpe_engine_state_write "$MPE_ENGINE_NAME" none failed "graph-restart" "$looper_label"
        return 1
    fi

    if ! mpe_wait_for_jack_server "$timeout"; then
        mpe_planned_promote_flag_clear
        mpe_engine_state_write "$MPE_ENGINE_NAME" none failed "no-server" "$looper_label"
        return 1
    fi

    mpe_engine_state_write "$MPE_ENGINE_NAME" jack recovering "promote-planned" "$looper_label"
    mpe_systemctl reset-failed "$surge_service" >/dev/null 2>&1 || true
    if ! mpe_systemctl restart "$surge_service"; then
        mpe_planned_promote_flag_clear
        mpe_engine_state_write "$MPE_ENGINE_NAME" jack failed "promote-failed" "$looper_label"
        return 1
    fi

    if ! mpe_wait_for_surge_on_graph "$timeout"; then
        mpe_planned_promote_flag_clear
        mpe_engine_state_write "$MPE_ENGINE_NAME" jack recovering "promote-timeout" "$looper_label"
        return 1
    fi

    mpe_restart_looper_after_graph_change "$reason"

    mpe_engine_reconcile_reset
    mpe_engine_state_write "$MPE_ENGINE_NAME" jack ok "" "$looper_label"
    mpe_planned_promote_flag_clear
    return 0
}

# jackd restart leaves SooperLooper as an orphan: the process survives but its
# JACK client is gone, so /hit and /set vanish while /get still answers. Buffer
# and sample-rate changes go through mpe_promote_surge_planned — restart SL here
# so the looper rejoins the bus instead of failing silently mid-set.
mpe_restart_looper_after_graph_change() {
    local reason="${1:-planned-graph-change}"
    if ! pgrep -x sooperlooper >/dev/null 2>&1; then
        return 0
    fi

    # shellcheck source=paths.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/paths.sh"
    local script="${MPE_MODULE_REPO}/scripts/sooperlooper/restart-sooperlooper.sh"
    if [ ! -x "$script" ]; then
        echo "audio-engine: sooperlooper running but ${script} missing — orphan likely after jackd restart ($reason)" >&2
        return 1
    fi

    echo "audio-engine: restarting SooperLooper after graph change ($reason) — recorded loops are cleared" >&2
    local owner
    owner="$(mpe_jack_graph_user)"
    if [ "$(id -un)" = "$owner" ]; then
        bash "$script"
    else
        sudo -u "$owner" -E bash "$script"
    fi
}

# ---------------------------------------------------------------------------
# Supervisor cooldown (spec D3)
# ---------------------------------------------------------------------------
#
# The watchdog polls every 5 s while Surge has RestartSec=10 and StartLimitBurst=5.
# An uncooled supervisor exhausts the burst budget in ~25 s and leaves Surge dead
# until manual intervention — worse than the fault it responds to. Hence: first
# restart immediate, then a cooldown between subsequent supervisor restarts, never
# while jackd is still settling, and escalate to state=failed after 3 tries.
#
# Start-limit window matches surge-xt-cli.service [Unit]: StartLimitBurst=5 over
# StartLimitIntervalSec=300. Cooldown is sized to stay inside that budget while
# keeping unplanned recovery retries practical for the operator (30 s default).

MPE_ENGINE_COOLDOWN_DEFAULT=30
# 15s was sized for an ALSA-contention hazard (Surge holding the tier device
# on the fallback path while jackd tried to reclaim it) that no longer exists
# — ALSA is not a reachable engine at all now. jackd itself is typically ready
# in ~6s on the Sound Blaster Play! 3; 5s just clears that plus the watchdog's
# 5s poll cycle without needlessly serializing recovery behind the old margin.
MPE_ENGINE_JACKD_SETTLE_DEFAULT=5
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
        "restarts=$count" || true
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
    mpe_jack_lsp 2>/dev/null | grep -qi 'surge'
}

# Back-compat alias used by udev helper and profile scripts.
restart_audio_graph() {
    mpe_restart_audio_graph
}
