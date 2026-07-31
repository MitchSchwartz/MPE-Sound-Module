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
    if [ "${MPE_AUDIO_PROFILE:-standalone}" = "usb-host" ]; then
        sudo systemctl enable --now usb-audio-gadget.service 2>/dev/null || true
        echo "  USB audio gadget: enabled (MPE_AUDIO_PROFILE=usb-host)"
    else
        sudo systemctl disable --now usb-audio-gadget.service 2>/dev/null || true
        echo "  USB audio gadget: disabled (MPE_AUDIO_PROFILE=${MPE_AUDIO_PROFILE:-standalone})"
    fi
}

mpe_enable_core_services() {
    mpe_enable_usb_audio_gadget
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
}
