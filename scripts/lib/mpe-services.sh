#!/bin/bash
# Enable/restart the correct patch browser UI based on MPE_UI_MODE (oled | touch).

_mpe_ui_mode_normalized() {
    case "${MPE_UI_MODE:-oled}" in
        touch) echo touch ;;
        oled | encoder | *) echo oled ;;
    esac
}

mpe_patch_browser_unit() {
    if [ "$(_mpe_ui_mode_normalized)" = touch ]; then
        echo touch-patch-browser.service
    else
        echo patch-browser.service
    fi
}

# Read one KEY=value from /etc/mpe/mpe.env (appliance canon — not repo config/mpe.env).
mpe_read_appliance_env_var() {
    local key="$1"
    local file="/etc/mpe/mpe.env"
    [ -f "$file" ] || return 1
    grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'"
}

# Reload runtime profile from the appliance env file.
mpe_source_appliance_env() {
    if [ -n "${MPE_ENV_FILE+x}" ]; then
        if [ -n "$MPE_ENV_FILE" ] && [ -f "$MPE_ENV_FILE" ]; then
            # shellcheck disable=SC1091
            set -a
            source "$MPE_ENV_FILE"
            set +a
        fi
        MPE_AUDIO_PROFILE="${MPE_AUDIO_PROFILE:-standalone}"
        export MPE_AUDIO_PROFILE
        # shellcheck source=audio-engine.sh
        source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/audio-engine.sh"
        mpe_export_synced_buffer_env
        return 0
    fi
    if [ ! -f /etc/mpe/mpe.env ]; then
        return 0
    fi
    # shellcheck disable=SC1091
    set -a
    source /etc/mpe/mpe.env
    set +a
    MPE_AUDIO_PROFILE="${MPE_AUDIO_PROFILE:-standalone}"
    export MPE_AUDIO_PROFILE
    # shellcheck source=audio-engine.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/audio-engine.sh"
    mpe_export_synced_buffer_env
}

mpe_retire_touch_shutdown_animation_unit() {
    local legacy=touch-shutdown-animation.service
    local shipped="${MPE_MODULE_REPO:-}/config/touch-shutdown-animation.service"
    sudo systemctl disable --now "$legacy" 2>/dev/null || true
    if [ ! -f "$shipped" ] && [ -f "/etc/systemd/system/$legacy" ]; then
        sudo rm -f "/etc/systemd/system/$legacy"
        echo "  Removed stale $legacy (use mpe-shutdown-splash.service)"
        sudo systemctl daemon-reload
    fi
}


mpe_enable_patch_browser_ui() {
    local mode unit other
    mode="$(_mpe_ui_mode_normalized)"
    if [ "$mode" = touch ]; then
        unit=touch-patch-browser.service
        other=patch-browser.service
        mpe_retire_touch_shutdown_animation_unit
        sudo systemctl disable --now boot-animation.service shutdown-animation.service 2>/dev/null || true
        sudo systemctl enable touch-boot-animation.service mpe-shutdown-splash.service 2>/dev/null || true
    else
        unit=patch-browser.service
        other=touch-patch-browser.service
        sudo systemctl disable --now touch-boot-animation.service mpe-shutdown-splash.service 2>/dev/null || true
        sudo systemctl enable boot-animation.service shutdown-animation.service 2>/dev/null || true
    fi

    sudo systemctl disable --now "$other" 2>/dev/null || true
    sudo systemctl enable --now "$unit"
    echo "  Patch browser UI: $mode ($unit enabled, $other disabled)"
}

mpe_enable_usb_audio_gadget() {
    # shellcheck source=lib/gadget-persist.sh
    source "$SCRIPT_DIR/lib/gadget-persist.sh"
    # Host-route watcher starts after Surge via surge-xt-cli.service ExecStartPost
    # (start-uac2-watchdog-if-needed.sh). Never start it here — boot sync and profile
    # switches both run before Surge is listening.

    if [ "${MPE_AUDIO_PROFILE:-standalone}" = "usb-host" ] \
        || [ "${MPE_AUDIO_PROFILE:-standalone}" = "usb-host-session" ]; then
        sudo systemctl enable --now usb-audio-gadget.service 2>/dev/null || true
        sudo systemctl enable uac2-stall-watchdog.service 2>/dev/null || true
        echo "  USB audio gadget: enabled (MPE_AUDIO_PROFILE=${MPE_AUDIO_PROFILE})"
        echo "  UAC2 host-route watcher: enabled (starts after Surge)"
    else
        sudo systemctl disable --now uac2-stall-watchdog.service 2>/dev/null || true
        sudo systemctl disable --now mic-to-uac2-bridge.service 2>/dev/null || true
        if mpe_gadget_persist_enabled; then
            sudo systemctl enable --now usb-audio-gadget.service 2>/dev/null || true
            echo "  USB audio gadget: kept bound (MPE_USB_GADGET_PERSIST=1; Surge on analog)"
        else
            sudo systemctl disable --now usb-audio-gadget.service 2>/dev/null || true
            echo "  USB audio gadget: disabled (MPE_USB_GADGET_PERSIST=0)"
        fi
    fi
}

mpe_enable_audio_profile_sync() {
    sudo systemctl enable mpe-audio-profile-sync.service 2>/dev/null || true
}

mpe_enable_core_services() {
    mpe_enable_usb_audio_gadget
    mpe_enable_audio_profile_sync
    sudo systemctl enable mpe-cpu-governor.service 2>/dev/null || true
    sudo systemctl enable --now mpe-jackd.service 2>/dev/null || true
    sudo systemctl enable --now mpe-pressure-remap.service 2>/dev/null || true
    if [ "$(mpe_read_appliance_env_var MPE_MIDI_CLOCK_IN_ENABLED 2>/dev/null || echo 1)" = "1" ]; then
        sudo systemctl enable --now midi-clock-in.service 2>/dev/null || true
    fi
    sudo systemctl enable --now surge-poly-governor.service 2>/dev/null || true
    sudo systemctl enable --now surge-xt-cli.service 2>/dev/null || true
    sudo systemctl enable surge-watchdog.service 2>/dev/null || true
    mpe_enable_patch_browser_ui
}

mpe_restart_core_services() {
    local browser
    browser="$(mpe_patch_browser_unit)"
    sudo systemctl restart surge-xt-cli.service 2>/dev/null || true
    if [ "$(_mpe_ui_mode_normalized)" = oled ]; then
        sudo systemctl restart boot-animation.service 2>/dev/null || true
    elif [ "$(_mpe_ui_mode_normalized)" = touch ]; then
        sudo systemctl restart touch-boot-animation.service 2>/dev/null || true
    fi
    if systemctl is-enabled --quiet "$browser" 2>/dev/null; then
        sudo systemctl restart "$browser" 2>/dev/null || true
    else
        sudo systemctl start "$browser" 2>/dev/null || true
    fi
    mpe_restart_peak_meter
}

# mpe-peak-meter is PartOf=mpe-jackd.service. PartOf propagates BOTH stop and
# restart, so an ordinary `systemctl restart mpe-jackd` brings the meter back on
# its own -- and every path in audio-engine.sh uses restart. Those are fine.
#
# The hole is a jackd STOP or death:
#   * `systemctl stop mpe-jackd` stops the meter and starts nothing
#   * a jackd crash or kill makes the meter's client exit 0, so
#     Restart=on-failure never fires
#
# Either way the unit sits inactive with Result=success and ExecMainStatus=0,
# looking entirely healthy, while the meter reads zero forever -- indistinguishable
# from real silence. Found 2026-09-02 when Mitch noticed his meter was off after
# measure-dac-loopback.sh stopped the graph outright.
#
# Start (not restart) and only when enabled: the unit is opt-in, gated on
# MPE_PEAK_METER=1, and starting a disabled one would defeat that. `start` on an
# already-running unit is a no-op, which is what we want here.
mpe_restart_peak_meter() {
    systemctl is-enabled --quiet mpe-peak-meter.service 2>/dev/null || return 0
    sudo systemctl start mpe-peak-meter.service 2>/dev/null || true
}
