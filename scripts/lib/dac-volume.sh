#!/bin/bash
# Sound Blaster Play! 3 hardware Speaker control (card-local, post-JACK).
#
# The touch Vol fader still trims Surge amp/volume in software. This layer sets
# the USB DAC's analog/digital output stage — what actually reaches headphones.
#
# Scale (measured on Pi, 2026-08-17): raw 0–88, dB ≈ (raw - 88) * 0.5
#   48 → -20 dB (conservative)   64 → -12 dB (appliance default)   76 → -6 dB

MPE_DAC_SPEAKER_CONTROL="${MPE_DAC_SPEAKER_CONTROL:-Speaker}"
MPE_DAC_SPEAKER_RAW_MAX="${MPE_DAC_SPEAKER_RAW_MAX:-88}"
MPE_DAC_VOLUME_DB_DEFAULT="${MPE_DAC_VOLUME_DB_DEFAULT:--12}"

sound_blaster_card_index() {
    local cards_file="${MPE_ASOUND_CARDS:-/proc/asound/cards}"
    [ -r "$cards_file" ] || return 1
    grep -iF "Sound Blaster Play! 3" "$cards_file" 2>/dev/null | awk '{print $1; exit}'
}

# Convert target dBFS-style attenuation to raw Speaker step (Sound Blaster only).
dac_speaker_raw_from_db() {
    local db="$1"
    local raw
    raw="$(awk -v db="$db" -v max="$MPE_DAC_SPEAKER_RAW_MAX" 'BEGIN {
        r = max + (db * 2.0);
        if (r < 0) r = 0;
        if (r > max) r = max;
        printf "%.0f", r;
    }')"
    echo "$raw"
}

dac_speaker_db_from_raw() {
    local raw="$1"
    awk -v raw="$raw" -v max="$MPE_DAC_SPEAKER_RAW_MAX" 'BEGIN {
        printf "%.2f", (raw - max) * 0.5;
    }'
}

mpe_resolve_dac_speaker_raw() {
    local raw="${MPE_DAC_SPEAKER_RAW:-}"
    local db="${MPE_DAC_VOLUME_DB:-}"
    if [ -n "$raw" ]; then
        echo "$raw"
        return 0
    fi
    if [ -n "$db" ]; then
        dac_speaker_raw_from_db "$db"
        return 0
    fi
    dac_speaker_raw_from_db "$MPE_DAC_VOLUME_DB_DEFAULT"
}

mpe_apply_dac_volume() {
    local card raw db_display
    card="$(sound_blaster_card_index)"
    if [ -z "$card" ]; then
        echo "dac-volume: Sound Blaster Play! 3 not found — skipping" >&2
        return 0
    fi
    raw="$(mpe_resolve_dac_speaker_raw)"
    if ! amixer -c "$card" sset "$MPE_DAC_SPEAKER_CONTROL" "$raw" >/dev/null 2>&1; then
        echo "dac-volume: amixer failed (card=$card control=$MPE_DAC_SPEAKER_CONTROL raw=$raw)" >&2
        return 1
    fi
    db_display="$(dac_speaker_db_from_raw "$raw")"
    echo "dac-volume: card $card Speaker=$raw (${db_display} dB)"
}
