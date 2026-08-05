#!/bin/bash
# Host-gated UAC2 routing for usb-host profile.
# Surge opens the gadget PCM only while the host capture stream is active.

MPE_UAC2_HOST_STREAMING_FLAG="${MPE_UAC2_HOST_STREAMING_FLAG:-/run/mpe-uac2-host-streaming}"

uac2_host_streaming_mark() {
    if [ "$(id -u)" -eq 0 ]; then
        : >"$MPE_UAC2_HOST_STREAMING_FLAG"
        chmod 0644 "$MPE_UAC2_HOST_STREAMING_FLAG" 2>/dev/null || true
    else
        sudo sh -c ": >'$MPE_UAC2_HOST_STREAMING_FLAG' && chmod 0644 '$MPE_UAC2_HOST_STREAMING_FLAG'" 2>/dev/null || true
    fi
}

uac2_host_streaming_active() {
    [ -f "$MPE_UAC2_HOST_STREAMING_FLAG" ]
}

uac2_host_streaming_clear() {
    rm -f "$MPE_UAC2_HOST_STREAMING_FLAG" 2>/dev/null || sudo rm -f "$MPE_UAC2_HOST_STREAMING_FLAG" 2>/dev/null || true
}
