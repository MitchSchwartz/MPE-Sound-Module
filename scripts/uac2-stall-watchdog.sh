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
# shellcheck source=lib/uac2-recovery-state.sh
source "$SCRIPT_DIR/lib/uac2-recovery-state.sh"

SURGE_SERVICE="surge-xt-cli.service"
POLL_SECONDS="${MPE_UAC2_WATCHDOG_POLL:-1}"
STALL_POLLS="${MPE_UAC2_WATCHDOG_STALL_POLLS:-4}"
COOLDOWN_SECONDS="${MPE_UAC2_WATCHDOG_COOLDOWN:-20}"
GRACE_SECONDS="${MPE_UAC2_WATCHDOG_GRACE:-25}"
POST_RESTART_GRACE="${MPE_UAC2_WATCHDOG_POST_RESTART_GRACE:-5}"
FAST_PROBE_SECONDS="${MPE_UAC2_WATCHDOG_FAST_PROBE:-1}"
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
    uac2_recovery_set recovering
    # Skip the 15s USB-MIDI wait; this is a recovery restart, not a cold boot.
    profile_switch_flag_mark
    if [ "$(id -u)" -eq 0 ]; then
        systemctl restart --no-block "$SURGE_SERVICE"
    else
        sudo -n systemctl restart --no-block "$SURGE_SERVICE" 2>/dev/null ||
            log "WARN: could not restart $SURGE_SERVICE (no root / no passwordless sudo)"
    fi
}

# Surge/JUCE often wedges at boot before any host consumer; detect that at stream open.
writer_already_wedged() {
    local status_path="$1"
    local appl_a appl_b
    appl_a="$(uac2_appl_ptr "$status_path" 2>/dev/null || true)"
    [ -z "$appl_a" ] && return 1
    sleep "$FAST_PROBE_SECONDS"
    appl_b="$(uac2_appl_ptr "$status_path" 2>/dev/null || true)"
    [ -n "$appl_b" ] && [ "$appl_a" = "$appl_b" ]
}

log "=== UAC2 stall watchdog started (poll=${POLL_SECONDS}s, stall=${STALL_POLLS} polls) ==="

card=""
rate_numid=""
status_path=""
last_appl=""
stall_count=0
last_rate="0"
last_owner_pid=""
grace_until=0
post_restart_grace_until=0

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
        last_rate="0"
        last_appl=""
        stall_count=0
        uac2_recovery_clear
        continue
    fi

    if [ "$last_rate" = "0" ]; then
        last_appl=""
        stall_count=0
        if writer_already_wedged "$status_path"; then
            log "Host stream opened @ ${rate}Hz but writer already wedged — immediate Surge restart"
            restart_surge
            post_restart_grace_until=$((SECONDS + POST_RESTART_GRACE))
            sleep "$COOLDOWN_SECONDS"
            last_rate="$rate"
            continue
        fi
        grace_until=$((SECONDS + GRACE_SECONDS))
        log "Host stream opened @ ${rate}Hz — stall grace ${GRACE_SECONDS}s"
    fi
    last_rate="$rate"

    owner_pid="$(awk '/owner_pid/{print $3; exit}' "$status_path" 2>/dev/null || true)"
    if [ -n "$owner_pid" ] && [ "$owner_pid" != "$last_owner_pid" ]; then
        last_owner_pid="$owner_pid"
        last_appl=""
        stall_count=0
        if [ "$SECONDS" -lt "$post_restart_grace_until" ]; then
            grace_until="$post_restart_grace_until"
            log "UAC2 owner PID $owner_pid — post-restart grace ${POST_RESTART_GRACE}s"
        else
            grace_until=$((SECONDS + GRACE_SECONDS))
            log "UAC2 owner PID $owner_pid — stall grace ${GRACE_SECONDS}s"
        fi
    fi

    if [ "$SECONDS" -lt "$grace_until" ]; then
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

    if [ -z "$last_appl" ]; then
        stall_count=0
    elif [ "$appl" = "$last_appl" ]; then
        stall_count=$((stall_count + 1))
    else
        stall_count=0
        uac2_recovery_clear
    fi
    last_appl="$appl"

    if [ "$stall_count" -ge "$STALL_POLLS" ]; then
        log "Surge write wedged (appl_ptr stuck at $appl for $((stall_count * POLL_SECONDS))s, host streaming @ ${rate}Hz) — restarting $SURGE_SERVICE"
        restart_surge
        post_restart_grace_until=$((SECONDS + POST_RESTART_GRACE))
        sleep "$COOLDOWN_SECONDS"
        last_appl=""
        stall_count=0
    fi
done
