#!/bin/bash
# Surge Watchdog: crash recovery + JACK/ALSA engine reconciliation (spec D3).

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
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
    local now decision last count jackd_start looper_label
    now=$(date +%s)
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
            log "RECONCILE FAILED: $count supervisor restarts without ok — stopping ($reason)"
            mpe_engine_state_write "$(mpe_audio_engine)" "$(mpe_engine_state_get active)" failed "supervisor-exhausted" "$looper_label"
            return 1
            ;;
    esac

    log "RECONCILE restarting Surge ($reason)"
    mpe_engine_reconcile_record_restart
    mpe_engine_state_write "$(mpe_audio_engine)" "$(mpe_engine_state_get active)" recovering "$reason" "$looper_label"
    sudo systemctl restart "$SURGE_SERVICE"
    return 0
}

_reconcile_engine() {
    local waited looper_label requested

    if ! mpe_engine_is_jack; then
        return 0
    fi

    looper_label="$(mpe_looper_state_label)"
    requested="$(mpe_audio_engine)"

    if mpe_surge_on_jack_graph; then
        mpe_engine_state_write "$requested" jack ok "" "$looper_label"
        mpe_engine_reconcile_reset
        return 0
    fi

    if ! mpe_jack_server_ready; then
        waited=0
        while [ "$waited" -lt "$RECONCILE_BUDGET" ]; do
            if mpe_jack_server_ready; then
                break
            fi
            sleep 1
            waited=$((waited + 1))
        done
        if ! mpe_jack_server_ready; then
            _supervisor_restart_surge "jackd-down"
            return 0
        fi
    fi

    if mpe_jack_server_ready && ! mpe_surge_on_jack_graph; then
        _supervisor_restart_surge "promote-to-jack"
    fi
}

log "=== Surge Watchdog Started (engine=$(mpe_audio_engine)) ==="

while true; do
    if systemctl is-failed "$SURGE_SERVICE" &>/dev/null; then
        log "ALERT: Surge service failed, cleaning user defaults"

        if [ -f "$USER_DEFAULTS" ]; then
            BACKUP="${USER_DEFAULTS}.corrupted_$(date +%Y%m%d_%H%M%S)"
            mv "$USER_DEFAULTS" "$BACKUP"
            log "Backed up corrupted file to: $BACKUP"
        fi

        sudo systemctl reset-failed "$SURGE_SERVICE"
        sudo systemctl restart "$SURGE_SERVICE"
        log "Service restarted"

        sleep 2
        if [ -f "$USER_DEFAULTS" ]; then
            chmod 644 "$USER_DEFAULTS" || true
            log "Set user defaults to writable (644) for OSC patch loading"
        fi
    fi

    _reconcile_engine

    sleep 5
done
