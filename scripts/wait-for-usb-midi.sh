#!/bin/bash
# Wait for Roli Seaboard BLOCK to enumerate and stabilize
# Non-blocking - allows Surge to start even if Roli not found
# Waits for device to be stable (seen multiple times) before proceeding

TIMEOUT=15  # Increased timeout for slower USB enumeration
ROLI_VID="2af4"
ROLI_PID="0700"
MAX_CHECKS=30  # 30 * 0.5s = 15s
STABLE_REQUIRED=3  # Device must be seen 3 times in a row to be considered stable

# Fast path: if Roli already detected and stable, exit immediately
stable_count=0
for i in $(seq 1 3); do
    if lsusb | grep -q "$ROLI_VID:$ROLI_PID"; then
        stable_count=$((stable_count + 1))
    else
        stable_count=0
    fi
    sleep 0.2
done

if [ $stable_count -ge $STABLE_REQUIRED ]; then
    echo "$(date): Roli Seaboard already detected and stable"
    exit 0
fi

# Poll for Roli enumeration and wait for stability
stable_count=0
for i in $(seq 1 $MAX_CHECKS); do
    if lsusb | grep -q "$ROLI_VID:$ROLI_PID"; then
        stable_count=$((stable_count + 1))
        if [ $stable_count -ge $STABLE_REQUIRED ]; then
            echo "$(date): Roli Seaboard detected and stable after $((i * 500))ms"
            # Give it one more moment to fully initialize
            sleep 0.5
            exit 0
        fi
    else
        stable_count=0  # Reset if device disappears
    fi
    sleep 0.5
done

echo "$(date): WARNING - Roli not detected/stabilized after ${TIMEOUT}s, proceeding anyway (udev will handle connection later)"
exit 0  # Non-blocking
