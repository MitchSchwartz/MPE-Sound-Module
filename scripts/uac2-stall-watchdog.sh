#!/bin/bash
# usb-host audio watchdog: lazy UAC2 route + stall recovery fallback.
#
# Lazy route (default): Surge boots on Sound Blaster; when the host opens capture,
# restart Surge on the UAC2 gadget while the host is already consuming — avoids the
# Surge/JUCE boot wedge entirely.
#
# Fallback: if Surge is already on UAC2 and appl_ptr freezes while the host streams,
# restart Surge (operational recovery for upstream ALSA writer stall).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/uac2-card.sh
source "$SCRIPT_DIR/lib/uac2-card.sh"
# shellcheck source=lib/profile-switch-flag.sh
source "$SCRIPT_DIR/lib/profile-switch-flag.sh"
# shellcheck source=lib/uac2-lazy-route.sh
source "$SCRIPT_DIR/lib/uac2-lazy-route.sh"
# shellcheck source=lib/uac2-recovery-state.sh
source "$SCRIPT_DIR/lib/uac2-recovery-state.sh"

SURGE_SERVICE="surge-xt-cli.service"
POLL_SECONDS="${MPE_UAC2_WATCHDOG_POLL:-1}"
STALL_POLLS="${MPE_UAC2_WATCHDOG_STALL_POLLS:-4}"
COOLDOWN_SECONDS="${MPE_UAC2_WATCHDOG_COOLDOWN:-20}"
POST_RESTART_GRACE="${MPE_UAC2_WATCHDOG_POST_RESTART_GRACE:-5}"
FAST_PROBE_SECONDS="${MPE_UAC2_WATCHDOG_FAST_PROBE:-1}"
# hw/appl gap threshold — one Surge buffer is often 4096 frames at stream open.
WEDGE_HW_GAP="${MPE_UAC2_WATCHDOG_HW_GAP:-8192}"
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

switch_surge_to_uac2() {
    uac2_force_output_mark
    restart_surge
}

# Surge/JUCE often wedges at boot before any host consumer; detect at stream open.
# appl_ptr may bump once when the host connects, then freeze — a single 1s probe misses that.
writer_already_wedged() {
    local status_path="$1"
    local appl_a appl_b appl_c hw_a hw_b hw_delta appl_delta
    appl_a="$(uac2_appl_ptr "$status_path" 2>/dev/null || true)"
    hw_a="$(uac2_hw_ptr "$status_path" 2>/dev/null || true)"
    [ -z "$appl_a" ] && return 1
    sleep "$FAST_PROBE_SECONDS"
    appl_b="$(uac2_appl_ptr "$status_path" 2>/dev/null || true)"
    hw_b="$(uac2_hw_ptr "$status_path" 2>/dev/null || true)"
    if [ -n "$appl_b" ] && [ "$appl_a" = "$appl_b" ]; then
        return 0
    fi
    if [ -n "$hw_a" ] && [ -n "$hw_b" ] && [ -n "$appl_b" ]; then
        hw_delta=$((hw_b - hw_a))
        appl_delta=$((appl_b - appl_a))
        if [ "$hw_delta" -ge "$WEDGE_HW_GAP" ] && [ "$appl_delta" -lt "$WEDGE_HW_GAP" ]; then
            sleep "$FAST_PROBE_SECONDS"
            appl_c="$(uac2_appl_ptr "$status_path" 2>/dev/null || true)"
            [ -n "$appl_c" ] && [ "$appl_b" = "$appl_c" ] && return 0
        fi
    fi
    return 1
}

log "=== UAC2 watchdog started (lazy=${MPE_UAC2_LAZY_ROUTE:-1}, poll=${POLL_SECONDS}s) ==="

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

    stream_just_opened=0
    if [ "$last_rate" = "0" ]; then
        stream_just_opened=1
        last_appl=""
        stall_count=0
        if uac2_lazy_route_enabled && ! surge_on_uac2_output; then
            log "Host stream opened @ ${rate}Hz — lazy route: switching Surge to UAC2"
            switch_surge_to_uac2
            post_restart_grace_until=$((SECONDS + POST_RESTART_GRACE))
            sleep "$COOLDOWN_SECONDS"
            last_rate="$rate"
            continue
        fi
        if writer_already_wedged "$status_path"; then
            log "Host stream opened @ ${rate}Hz but UAC2 writer wedged — restarting Surge"
            restart_surge
            post_restart_grace_until=$((SECONDS + POST_RESTART_GRACE))
            sleep "$COOLDOWN_SECONDS"
            last_rate="$rate"
            continue
        fi
        grace_until=$SECONDS
        log "Host stream opened @ ${rate}Hz — Surge already on UAC2"
    fi
    last_rate="$rate"

    owner_pid="$(awk '/owner_pid/{print $3; exit}' "$status_path" 2>/dev/null || true)"
    if [ -n "$owner_pid" ] && [ "$owner_pid" != "$last_owner_pid" ]; then
        last_owner_pid="$owner_pid"
        last_appl=""
        stall_count=0
        if writer_already_wedged "$status_path"; then
            log "UAC2 owner PID $owner_pid wedged — immediate Surge restart"
            restart_surge
            post_restart_grace_until=$((SECONDS + POST_RESTART_GRACE))
            sleep "$COOLDOWN_SECONDS"
            continue
        fi
        if [ "$stream_just_opened" -eq 0 ] && [ "$SECONDS" -lt "$post_restart_grace_until" ]; then
            grace_until="$post_restart_grace_until"
            log "UAC2 owner PID $owner_pid — post-restart grace ${POST_RESTART_GRACE}s"
        else
            grace_until=$SECONDS
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
