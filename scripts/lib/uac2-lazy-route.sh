#!/bin/bash
# usb-host lazy route: boot Surge on Sound Blaster; switch to UAC2 when host captures.

MPE_FORCE_UAC2_FLAG="${MPE_FORCE_UAC2_FLAG:-/run/mpe-force-uac2-output}"
MPE_SURGE_AUDIO_ROUTE_FILE="${MPE_SURGE_AUDIO_ROUTE_FILE:-/run/mpe-surge-audio-route}"

uac2_lazy_route_enabled() {
    [ "${MPE_UAC2_LAZY_ROUTE:-1}" != "0" ]
}

uac2_force_output_mark() {
    if [ "$(id -u)" -eq 0 ]; then
        : >"$MPE_FORCE_UAC2_FLAG"
        chmod 0644 "$MPE_FORCE_UAC2_FLAG" 2>/dev/null || true
    else
        sudo sh -c ": >'$MPE_FORCE_UAC2_FLAG' && chmod 0644 '$MPE_FORCE_UAC2_FLAG'" 2>/dev/null || true
    fi
}

uac2_force_output_active() {
    [ -f "$MPE_FORCE_UAC2_FLAG" ]
}

uac2_force_output_clear() {
    rm -f "$MPE_FORCE_UAC2_FLAG" 2>/dev/null || sudo rm -f "$MPE_FORCE_UAC2_FLAG" 2>/dev/null || true
}

surge_audio_route_write() {
    local route="${1:?route required (analog|uac2)}"
    if [ "$(id -u)" -eq 0 ]; then
        printf '%s\n' "$route" >"$MPE_SURGE_AUDIO_ROUTE_FILE"
        chmod 0644 "$MPE_SURGE_AUDIO_ROUTE_FILE" 2>/dev/null || true
    else
        printf '%s\n' "$route" | sudo tee "$MPE_SURGE_AUDIO_ROUTE_FILE" >/dev/null 2>&1 || true
    fi
}

surge_audio_route_read() {
    if [ -r "$MPE_SURGE_AUDIO_ROUTE_FILE" ]; then
        tr -d '[:space:]' <"$MPE_SURGE_AUDIO_ROUTE_FILE"
        return 0
    fi
    echo "analog"
}

surge_on_uac2_output() {
    [ "$(surge_audio_route_read)" = "uac2" ]
}
