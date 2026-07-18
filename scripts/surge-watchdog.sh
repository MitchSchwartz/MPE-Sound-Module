#!/bin/bash
# Surge Watchdog: Monitors for crashes and auto-cleans corrupted user defaults
# This runs as a systemd service and watches the surge-xt-cli service

SURGE_SERVICE="surge-xt-cli.service"
USER_DEFAULTS="/home/mitch/.local/share/Surge XT/SurgeXTUserDefaults.xml"
LOG_FILE="/home/mitch/surge-watchdog.log"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >> "$LOG_FILE"
    echo "$1"
}

log "=== Surge Watchdog Started ==="

# Watch for service failures
while true; do
    # Check if surge service is failed
    if systemctl is-failed "$SURGE_SERVICE" &>/dev/null; then
        log "ALERT: Surge service failed, cleaning user defaults"

        # Backup corrupted file
        if [ -f "$USER_DEFAULTS" ]; then
            BACKUP="${USER_DEFAULTS}.corrupted_$(date +%Y%m%d_%H%M%S)"
            mv "$USER_DEFAULTS" "$BACKUP"
            log "Backed up corrupted file to: $BACKUP"
        fi

        # Restart the service
        sudo systemctl reset-failed "$SURGE_SERVICE"
        sudo systemctl restart "$SURGE_SERVICE"
        log "Service restarted"

        # Set file to writable after restart (OSC patch loading requires write access)
        sleep 2  # Wait for Surge to create fresh user defaults
        if [ -f "$USER_DEFAULTS" ]; then
            chmod 644 "$USER_DEFAULTS" || true
            log "Set user defaults to writable (644) for OSC patch loading"
        fi
    fi

    # Sleep for 5 seconds before checking again
    sleep 5
done
