#!/bin/bash
# Resolve the configfs UAC2 gadget ALSA card index and its PCM status/control paths.
# Card index shifts with hotplug order — always resolve, never hardcode.

MPE_UAC2_ASOUND_ROOT="${MPE_UAC2_ASOUND_ROOT:-/proc/asound}"

uac2_card_index() {
    local line cards_file="${MPE_UAC2_ASOUND_ROOT}/cards"
    line="$(grep -iE '\[(UAC2Gadget|UAC2_Gadget)' "$cards_file" 2>/dev/null | head -1)"
    if [ -z "$line" ]; then
        line="$(grep -iE 'UAC2|USB Audio Passthrough|MPE Sound Module' "$cards_file" 2>/dev/null | head -1)"
    fi
    [ -z "$line" ] && return 1
    echo "$line" | awk '{print $1}'
}

uac2_pcm_status_path() {
    local card="${1:-}"
    [ -z "$card" ] && card="$(uac2_card_index)"
    [ -z "$card" ] && return 1
    printf '%s/card%s/pcm0p/sub0/status' "$MPE_UAC2_ASOUND_ROOT" "$card"
}

# Frames written by the app (Surge). Frozen while hw_ptr advances == wedged writer.
uac2_appl_ptr() {
    local status="${1:?status path required}"
    [ -r "$status" ] || return 1
    awk '/appl_ptr/{print $3}' "$status" 2>/dev/null
}

# Hardware read pointer — keeps advancing while the host consumes even if appl_ptr froze.
uac2_hw_ptr() {
    local status="${1:?status path required}"
    [ -r "$status" ] || return 1
    awk '/hw_ptr/{print $3}' "$status" 2>/dev/null
}

# numid of the read-only 'Playback Rate' PCM control.
uac2_rate_numid() {
    local card="${1:?card required}"
    amixer -c "$card" controls 2>/dev/null |
        sed -n "s/^numid=\([0-9]*\),iface=PCM,name='Playback Rate'.*/\1/p" | head -1
}

# 0 when the USB host has no active stream; sample rate (e.g. 48000) while it streams.
# This is the only reliable "host is consuming" signal — the UDC reads `configured`
# whenever the cable is attached, streaming or not.
uac2_host_stream_rate() {
    local card="${1:?card required}" numid="${2:-}"
    [ -z "$numid" ] && numid="$(uac2_rate_numid "$card")"
    [ -z "$numid" ] && return 1
    amixer -c "$card" cget "numid=$numid" 2>/dev/null |
        sed -n 's/.*: values=\([0-9]*\).*/\1/p' | head -1
}
