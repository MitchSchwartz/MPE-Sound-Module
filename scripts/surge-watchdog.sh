#!/bin/bash
# Surge Watchdog: Monitors for crashes and auto-cleans corrupted user defaults

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

SURGE_SERVICE="surge-xt-cli.service"
USER_DEFAULTS="$MPE_SURGE_USER_DEFAULTS"
LOG_FILE="${MPE_WATCHDOG_LOG:-$HOME/surge-watchdog.log}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >> "$LOG_FILE"
    echo "$1"
}

log "=== Surge Watchdog Started ==="

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

    sleep 5
done
