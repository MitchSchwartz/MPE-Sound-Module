#!/bin/bash
# Pick the DRM card driving a connected panel (Pi 4 → usually card0, Pi 5 → often card1).

detect_drm_card_device() {
    local entry status num
    for entry in /sys/class/drm/card[0-9]-*/status; do
        [ -f "$entry" ] || continue
        status="$(tr -d '[:space:]' < "$entry" 2>/dev/null || true)"
        [ "$status" = connected ] || continue
        num="$(basename "$(dirname "$entry")")"
        num="${num%%-*}"
        num="${num#card}"
        echo "/dev/dri/card${num}"
        return 0
    done
    if [ -e /dev/dri/card0 ]; then
        echo "/dev/dri/card0"
        return 0
    fi
    echo "/dev/dri/card1"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    detect_drm_card_device
fi
