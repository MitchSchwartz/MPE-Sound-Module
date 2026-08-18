#!/bin/bash
# Surge Watchdog: crash recovery + JACK graph reconciliation (spec D3).

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

SURGE_SERVICE="surge-xt-cli.service"
USER_DEFAULTS="$MPE_SURGE_USER_DEFAULTS"
LOG_FILE="${MPE_WATCHDOG_LOG:-$HOME/surge-watchdog.log}"
RECONCILE_BUDGET="${MPE_RECONCILE_BUDGET_SEC:-15}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >> "$LOG_FILE"
    echo "$1"
}

_supervisor_restart_surge() {
    local reason="$1"
    if mpe_planned_promote_flag_set; then
        return 1
    fi
    local now decision last count jackd_start looper_label
    now=$EPOCHSECONDS
    last=$(mpe_engine_reconcile_last_restart)
    count=$(mpe_engine_reconcile_count)
    jackd_start=$(mpe_jack_start_epoch)
    decision=$(mpe_engine_reconcile_decision "$now" "$last" "$count" "$jackd_start")
    looper_label="$(mpe_looper_state_label)"

    case "$decision" in
        cooldown)
            log "RECONCILE cooldown — not restarting Surge ($reason)"
            return 1
            ;;
        jackd-settling)
            log "RECONCILE jackd settling — not restarting Surge ($reason)"
            return 1
            ;;
        failed)
            # Poll is every 5 s and this state is terminal until the graph is
            # restarted, so announce it once rather than filling the log forever.
            if [ "$(mpe_engine_state_get state)" != failed ]; then
                log "RECONCILE FAILED: $count supervisor restarts without ok — stopping ($reason)"
                mpe_engine_state_write "$MPE_ENGINE_NAME" "$(mpe_engine_state_get active)" failed "supervisor-exhausted" "$looper_label"
            fi
            return 1
            ;;
    esac

    log "RECONCILE restarting Surge ($reason)"
    mpe_engine_reconcile_record_restart
    mpe_engine_state_write "$MPE_ENGINE_NAME" "$(mpe_engine_state_get active)" recovering "$reason" "$looper_label"
    mpe_systemctl reset-failed "$SURGE_SERVICE" >/dev/null 2>&1 || true
    mpe_systemctl restart "$SURGE_SERVICE"
    return 0
}

# Single-engine reconcile: on the graph → ok; server ready but Surge not on it
# → promote (restart Surge so it re-joins); server not ready → wait out the
# budget, then treat jackd as down and restart Surge (it will fail loud and
# retry on its own until jackd recovers — spec D3 amended 2026-08-13).
_reconcile_engine() {
    local waited looper_label state active

    looper_label="$(mpe_looper_state_label)"

    if mpe_planned_promote_flag_set; then
        return 0
    fi

    # Steady state: unit active and engine already ok — skip jack_lsp if a full
    # graph probe ran recently. Unbounded skip is indistinguishable from stopping
    # to look (orphaned JACK client, DECISIONS.md 2026-08-15).
    if systemctl is-active --quiet "$SURGE_SERVICE" 2>/dev/null; then
        state="$(mpe_engine_state_get state)"
        active="$(mpe_engine_state_get active)"
        if [ "$state" = ok ] && [ "$active" = jack ]            && [ $((EPOCHSECONDS - _last_jack_probe)) -lt "$JACK_PROBE_INTERVAL_S" ]; then
            return 0
        fi
    fi

    _last_jack_probe=$EPOCHSECONDS
    if mpe_surge_on_jack_graph; then
        mpe_engine_state_write "$MPE_ENGINE_NAME" jack ok "" "$looper_label"
        mpe_engine_reconcile_reset
        return 0
    fi

    if ! mpe_jack_server_ready; then
        waited=0
        while [ "$waited" -lt "$RECONCILE_BUDGET" ]; do
            sleep 1
            waited=$((waited + 1))
            if mpe_jack_server_ready; then
                break
            fi
        done
    fi

    if ! mpe_jack_server_ready; then
        _supervisor_restart_surge "jackd-down"
        return 0
    fi

    _supervisor_restart_surge "promote-to-jack"
}


LOOPER_RECONCILE_INTERVAL_S="${MPE_LOOPER_RECONCILE_INTERVAL_S:-30}"
_last_looper_reconcile=0

# Bound the healthy-path short-circuit: full graph probe at least once per interval.
# Default 10 s (116 ms / 10 s ≈ 1.16% of a core); must stay ≤ cooldown (30 s).
# _last_jack_probe=0 forces probe on first tick.
JACK_PROBE_INTERVAL_S="${MPE_JACK_PROBE_INTERVAL_S:-10}"
_last_jack_probe=0

# Batched systemctl pre-filter: fork Python (~400 ms on Pi) only when a looper
# unit is non-active. Steady state is one systemctl call (~22 ms) every 30 s.
_reconcile_looper_units_if_needed() {
    local script need=0 state
    local -a units=(
        mpe-sooperlooper.service
        mpe-looper-session.service
        sl-watchdog.service
    )
    script="${MPE_MODULE_REPO}/scripts/ensure-looper-units-running.py"

    while IFS= read -r state; do
        if [ "$state" != "active" ]; then
            need=1
            break
        fi
    done < <(systemctl is-active "${units[@]}" 2>/dev/null)

    [ "$need" = 0 ] && return 0
    [ -x "$script" ] || return 0
    python3 "$script" >/dev/null 2>&1 || true
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
log "=== Surge Watchdog Started (engine=jack) ==="

while true; do
    if systemctl is-failed "$SURGE_SERVICE" &>/dev/null; then
        engine_reason="$(mpe_engine_state_get reason)"
        case "$engine_reason" in
            no-server | no-jack-device)
                log "ALERT: Surge failed while graph unavailable (reason=$engine_reason) — not treating user defaults as corrupt"
                ;;
            *)
                log "ALERT: Surge service failed, cleaning user defaults"
                ;;
        esac

        if _supervisor_restart_surge "surge-failed"; then
            if [ "$engine_reason" != "no-server" ] && [ "$engine_reason" != "no-jack-device" ]; then
                if [ -f "$USER_DEFAULTS" ]; then
                    BACKUP="${USER_DEFAULTS}.corrupted_$(date +%Y%m%d_%H%M%S)"
                    mv "$USER_DEFAULTS" "$BACKUP"
                    log "Backed up corrupted file to: $BACKUP"
                fi
            fi
            sleep 2
            if [ -f "$USER_DEFAULTS" ]; then
                chmod 644 "$USER_DEFAULTS" || true
                log "Set user defaults to writable (644) for OSC patch loading"
            fi
        fi
    fi

    _reconcile_engine

    now=$EPOCHSECONDS
    if [ $((now - _last_looper_reconcile)) -ge "$LOOPER_RECONCILE_INTERVAL_S" ]; then
        _reconcile_looper_units_if_needed
        _last_looper_reconcile=$now
    fi

    sleep 5
done
fi
