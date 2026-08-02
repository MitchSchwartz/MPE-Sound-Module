#!/bin/bash
# Recover Surge from a wedged UAC2 gadget write (usb-host profile).
#
# Surge/JUCE's ALSA output thread blocks indefinitely once the USB host stops
# consuming the gadget stream, and never recovers when the host returns: appl_ptr
# freezes while hw_ptr keeps advancing, and the process drops to ~0 CPU. Since the
# host is usually not capturing when Surge starts at boot, Surge is already wedged
# by the time a DAW opens the input — the module appears silent over USB.
#
# Restart Surge only when the host IS streaming but the writer is frozen, so an
# idle (nothing connected) module never restart-loops.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/uac2-card.sh
source "$SCRIPT_DIR/lib/uac2-card.sh"
# shellcheck source=lib/profile-switch-flag.sh
source "$SCRIPT_DIR/lib/profile-switch-flag.sh"

SURGE_SERVICE="surge-xt-cli.service"
POLL_SECONDS="${MPE_UAC2_WATCHDOG_POLL:-1}"
STALL_POLLS="${MPE_UAC2_WATCHDOG_STALL_POLLS:-4}"
COOLDOWN_SECONDS="${MPE_UAC2_WATCHDOG_COOLDOWN:-20}"
WATCHDOG_LOG="${MPE_UAC2_WATCHDOG_LOG:-$HOME/uac2-stall-watchdog.log}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >>"$WATCHDOG_LOG" 2>/dev/null
    echo "$1"
}

if [ "${MPE_AUDIO_PROFILE:-standalone}" != "usb-host" ]; then
    log "Profile ${MPE_AUDIO_PROFILE:-standalone} — UAC2 stall watchdog not needed, exiting"
    exit 0
fi

restart_surge() {
    # Skip the 15s USB-MIDI wait; this is a recovery restart, not a cold boot.
    profile_switch_flag_mark
    if [ "$(id -u)" -eq 0 ]; then
        systemctl restart --no-block "$SURGE_SERVICE"
    else
        sudo -n systemctl restart --no-block "$SURGE_SERVICE" 2>/dev/null ||
            log "WARN: could not restart $SURGE_SERVICE (no root / no passwordless sudo)"
    fi
}

log "=== UAC2 stall watchdog started (poll=${POLL_SECONDS}s, stall=${STALL_POLLS} polls) ==="

card=""
rate_numid=""
status_path=""
last_appl=""
stall_count=0

while true; do
    sleep "$POLL_SECONDS"

    if [ -z "$card" ] || [ ! -r "$status_path" ]; then
        card="$(uac2_card_index)" || card=""
        if [ -z "$card" ]; then
            last_appl=""
            stall_count=0
            continue
        fi
        status_path="$(uac2_pcm_status_path "$card")"
        rate_numid="$(uac2_rate_numid "$card")"
        last_appl=""
        stall_count=0
    fi

    # Host not streaming: a frozen writer is expected, not a fault.
    rate="$(uac2_host_stream_rate "$card" "$rate_numid" 2>/dev/null || echo 0)"
    if [ -z "$rate" ] || [ "$rate" = "0" ]; then
        last_appl=""
        stall_count=0
        continue
    fi

    appl="$(uac2_appl_ptr "$status_path" 2>/dev/null || true)"
    if [ -z "$appl" ]; then
        last_appl=""
        stall_count=0
        continue
    fi

    if [ "$appl" = "$last_appl" ]; then
        stall_count=$((stall_count + 1))
    else
        stall_count=0
    fi
    last_appl="$appl"

    if [ "$stall_count" -ge "$STALL_POLLS" ]; then
        log "Surge write wedged (appl_ptr stuck at $appl for $((stall_count * POLL_SECONDS))s, host streaming @ ${rate}Hz) — restarting $SURGE_SERVICE"
        restart_surge
        sleep "$COOLDOWN_SECONDS"
        last_appl=""
        stall_count=0
    fi
done
