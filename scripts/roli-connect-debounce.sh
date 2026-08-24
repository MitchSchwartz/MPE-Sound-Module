#!/bin/bash
# Debounce ROLI USB connect/disconnect — restart mpe-pressure-remap when ports go stale.
#
# Surge reads Midi Through ← remapper ← controller. The remapper opens ALSA MIDI once
# at startup; after a controller power-cycle the old RtMidi port is dead even though USB
# and Surge look healthy.

ACTION="${1:-}"
LOCK_FILE="/tmp/roli-remap-restart.lock"
MIN_RESTART_INTERVAL=8
REMAP_SERVICE="mpe-pressure-remap.service"
LOG_FILE="/tmp/roli-events.log"
MIDI_CONNECT_STATE="${MPE_MIDI_CONNECT_STATE:-/run/mpe/midi-connect.state}"

log() {
    echo "$(date): $1" >>"$LOG_FILE"
}

midi_connect_begin() {
    local phase="${1:-connecting}"
    mkdir -p "$(dirname "$MIDI_CONNECT_STATE")"
    echo "$phase $(date +%s)" >"$MIDI_CONNECT_STATE"
    chmod 644 "$MIDI_CONNECT_STATE" 2>/dev/null || true
}

midi_connect_clear() {
    rm -f "$MIDI_CONNECT_STATE"
}

check_recent_restart() {
    if [ -f "$LOCK_FILE" ]; then
        local last_restart now elapsed
        last_restart=$(cat "$LOCK_FILE")
        now=$(date +%s)
        elapsed=$((now - last_restart))
        if [ "$elapsed" -lt "$MIN_RESTART_INTERVAL" ]; then
            log "Skipping remapper restart — last restart ${elapsed}s ago (min ${MIN_RESTART_INTERVAL}s)"
            return 1
        fi
    fi
    return 0
}

mark_restart() {
    date +%s >"$LOCK_FILE"
}

roli_usb_present() {
    lsusb | grep -qi "2af4:"
}

wait_for_stability() {
    local stable_count=0
    local required_stable=3

    for _ in $(seq 1 10); do
        if roli_usb_present; then
            stable_count=$((stable_count + 1))
            if [ "$stable_count" -ge "$required_stable" ]; then
                return 0
            fi
        else
            stable_count=0
        fi
        sleep 0.5
    done
    return 1
}

# True when LUMI/Seaboard ALSA port is wired into the remapper's RtMidiIn client.
remap_input_connected() {
    roli_usb_present || return 1
    aconnect -l 2>/dev/null | grep -A2 "RtMidiIn Client" | grep -q "Connected From"
}

restart_remapper() {
    if ! check_recent_restart; then
        return 0
    fi
    log "Restarting $REMAP_SERVICE"
    mark_restart
    systemctl restart --no-block "$REMAP_SERVICE"
}

case "$ACTION" in
    add)
        log "ROLI controller connected — waiting for USB stability"
        midi_connect_begin
        if ! wait_for_stability; then
            log "ROLI not stable after wait — skipping remapper restart"
            midi_connect_clear
            exit 0
        fi
        if remap_input_connected; then
            log "Remapper already connected to ROLI ALSA port — skipping restart"
            midi_connect_clear
            exit 0
        fi
        restart_remapper
        midi_connect_clear
        ;;
    remove)
        log "ROLI controller disconnected"
        midi_connect_begin disconnecting
        restart_remapper
        midi_connect_clear
        ;;
    *)
        echo "Usage: $0 add|remove" >&2
        exit 1
        ;;
esac

exit 0
