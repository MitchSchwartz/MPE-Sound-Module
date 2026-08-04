#!/bin/bash
# usb-host audio routing: gate UAC2 output on host capture stream rate.
#
# Surge must not hold an open UAC2 PCM unless the host is actively capturing.
# On capture open (rate 0 → 44100): restart Surge on the gadget.
# On capture close (rate → 0): restart Surge on idle output (Sound Blaster or Pi headphone).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/uac2-card.sh
source "$SCRIPT_DIR/lib/uac2-card.sh"
# shellcheck source=lib/profile-switch-flag.sh
source "$SCRIPT_DIR/lib/profile-switch-flag.sh"
# shellcheck source=lib/uac2-host-route.sh
source "$SCRIPT_DIR/lib/uac2-host-route.sh"
# shellcheck source=lib/uac2-recovery-state.sh
source "$SCRIPT_DIR/lib/uac2-recovery-state.sh"

SURGE_SERVICE="surge-xt-cli.service"
POLL_SECONDS="${MPE_UAC2_WATCHDOG_POLL:-1}"
COOLDOWN_SECONDS="${MPE_UAC2_WATCHDOG_COOLDOWN:-3}"
WATCHDOG_LOG="${MPE_UAC2_WATCHDOG_LOG:-$HOME/uac2-stall-watchdog.log}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $1" >>"$WATCHDOG_LOG" 2>/dev/null
    echo "$1"
}

if [ "${MPE_AUDIO_PROFILE:-standalone}" != "usb-host" ]; then
    log "Profile ${MPE_AUDIO_PROFILE:-standalone} — host-route watcher not needed, exiting"
    exit 0
fi

restart_surge() {
    uac2_recovery_set recovering
    profile_switch_flag_mark
    if [ "$(id -u)" -eq 0 ]; then
        systemctl restart --no-block "$SURGE_SERVICE"
    else
        sudo -n systemctl restart --no-block "$SURGE_SERVICE" 2>/dev/null ||
            log "WARN: could not restart $SURGE_SERVICE (no root / no passwordless sudo)"
    fi
}

host_is_streaming() {
    local rate="$1"
    [ -n "$rate" ] && [ "$rate" != "0" ]
}

log "=== UAC2 host-route watcher started (poll=${POLL_SECONDS}s) ==="

card=""
rate_numid=""
host_streaming=-1

while true; do
    sleep "$POLL_SECONDS"

    if [ -z "$card" ] || [ ! -r "$(uac2_pcm_status_path "$card" 2>/dev/null || echo "")" ]; then
        card="$(uac2_card_index)" || card=""
        if [ -z "$card" ]; then
            continue
        fi
        rate_numid="$(uac2_rate_numid "$card")"
    fi

    rate="$(uac2_host_stream_rate "$card" "$rate_numid" 2>/dev/null || echo 0)"
    if host_is_streaming "$rate"; then
        streaming=1
    else
        streaming=0
        uac2_recovery_clear
    fi

    if [ "$host_streaming" -lt 0 ]; then
        host_streaming=$streaming
        if [ "$streaming" -eq 1 ] && ! uac2_host_streaming_active; then
            uac2_host_streaming_mark
            log "Host already capturing @ ${rate}Hz — Surge → UAC2"
            restart_surge
            sleep "$COOLDOWN_SECONDS"
        elif [ "$streaming" -eq 0 ]; then
            uac2_host_streaming_clear
        fi
        continue
    fi

    if [ "$streaming" -eq "$host_streaming" ]; then
        continue
    fi

    host_streaming=$streaming
    if [ "$streaming" -eq 1 ]; then
        uac2_host_streaming_mark
        log "Host capture opened @ ${rate}Hz — Surge → UAC2"
    else
        uac2_host_streaming_clear
        log "Host capture closed — Surge → idle output"
    fi
    restart_surge
    sleep "$COOLDOWN_SECONDS"
done
