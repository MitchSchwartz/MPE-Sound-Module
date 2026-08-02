#!/bin/bash
# Wait until the configfs UAC2 gadget appears in ALSA (usb-host profile).
# shellcheck disable=SC2034
# Sourced by set-audio-profile.sh — not executed directly.

wait_for_uac2_gadget() {
    local timeout_s="${1:-8}"
    local deadline=$((SECONDS + timeout_s))

    while [ "$SECONDS" -lt "$deadline" ]; do
        if grep -qiE 'UAC2|Gadget|USB Audio' /proc/asound/cards 2>/dev/null; then
            return 0
        fi
        if aplay -l 2>/dev/null | grep -qiE 'UAC2|Gadget|USB Audio'; then
            sleep 0.25
            return 0
        fi
        sleep 0.2
    done

    echo "WARN: UAC2 gadget ALSA device not seen after ${timeout_s}s" >&2
    return 1
}
