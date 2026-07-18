#!/bin/bash
# Debounce script for Roli Seaboard connection events
# Prevents restart loops during USB enumeration by:
# 1. Waiting for device to stabilize
# 2. Checking if Surge was recently started
# 3. Only restarting if device is actually stable

ACTION="$1"  # "add" or "remove"
LOCK_FILE="/tmp/roli-restart.lock"
DEBOUNCE_TIME=3  # Wait 3 seconds for device to stabilize
MIN_RESTART_INTERVAL=10  # Don't restart if Surge was restarted in last 10 seconds

# Function to check if Surge was recently restarted
check_recent_restart() {
    if [ -f "$LOCK_FILE" ]; then
        local last_restart=$(cat "$LOCK_FILE")
        local now=$(date +%s)
        local elapsed=$((now - last_restart))
        
        if [ $elapsed -lt $MIN_RESTART_INTERVAL ]; then
            echo "$(date): Skipping restart - Surge was restarted $elapsed seconds ago (min interval: $MIN_RESTART_INTERVAL)"
            return 1  # Too recent, skip
        fi
    fi
    return 0  # OK to restart
}

# Function to wait for device stability
wait_for_stability() {
    local vid="2af4"
    local pid="0700"
    local stable_count=0
    local required_stable=3  # Device must be seen 3 times in a row
    
    for i in $(seq 1 10); do
        if lsusb | grep -q "$vid:$pid"; then
            stable_count=$((stable_count + 1))
            if [ $stable_count -ge $required_stable ]; then
                return 0  # Device is stable
            fi
        else
            stable_count=0  # Reset counter if device disappears
        fi
        sleep 0.5
    done
    
    return 1  # Device not stable
}

# Main logic
if [ "$ACTION" == "add" ]; then
    echo "$(date): Roli Seaboard connected, waiting for stability..." >> /tmp/roli-events.log
    
    # Wait for device to stabilize
    if wait_for_stability; then
        echo "$(date): Roli Seaboard stable, checking if restart needed..." >> /tmp/roli-events.log
        
        # Check if Surge is already running and device is connected
        if systemctl is-active --quiet surge-xt-cli.service; then
            # Check if device is actually available to Surge
            if aconnect -l 2>/dev/null | grep -qi "seaboard\|roli"; then
                echo "$(date): Roli already connected to Surge, skipping restart" >> /tmp/roli-events.log
                exit 0
            fi
        fi
        
        # Check if we should restart (debounce)
        if check_recent_restart; then
            echo "$(date): Restarting Surge XT CLI for Roli connection" >> /tmp/roli-events.log
            echo $(date +%s) > "$LOCK_FILE"
            systemctl restart surge-xt-cli.service
        else
            echo "$(date): Skipping restart (debounced)" >> /tmp/roli-events.log
        fi
    else
        echo "$(date): Roli Seaboard not stable, skipping restart" >> /tmp/roli-events.log
    fi
    
elif [ "$ACTION" == "remove" ]; then
    echo "$(date): Roli Seaboard disconnected" >> /tmp/roli-events.log
    
    # Only restart if Surge is running (device was actually in use)
    if systemctl is-active --quiet surge-xt-cli.service; then
        if check_recent_restart; then
            echo "$(date): Restarting Surge XT CLI after Roli disconnection" >> /tmp/roli-events.log
            echo $(date +%s) > "$LOCK_FILE"
            systemctl restart surge-xt-cli.service
        else
            echo "$(date): Skipping restart (debounced)" >> /tmp/roli-events.log
        fi
    fi
fi

exit 0

