#!/bin/bash
# ExecStartPre for mpe-jackd.service — pick the device, wait for it to exist.
#
# Spec D1/D2: device selection runs on EVERY jackd start (so a profile switch or
# a DAC replug re-evaluates it) and uses the same tier logic as Surge via
# detect-jack-device.sh → detect-audio-device.sh. The resolved card is handed to
# ExecStart through a file in /run/mpe rather than re-detected, so the two steps
# cannot disagree.
#
# Exits non-zero when no usable card appears. That fails the unit loudly, which
# is correct: Restart=always retries, and there is no ALSA fallback keeping the
# instrument audible in the meantime — the appliance stays silent and reports
# state=failed until a card appears (spec D3, amended 2026-08-13).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

CARDS_FILE="${MPE_ASOUND_CARDS:-/proc/asound/cards}"
DEVICE_FILE="${MPE_JACK_DEVICE_FILE:-$(mpe_run_dir)/jack-device}"
WAIT_SECONDS="${MPE_JACK_DEVICE_WAIT_S:-15}"

log() {
    echo "mpe-jackd prestart: $1"
}

# sound.target does not guarantee the USB DAC has enumerated — bounded wait for
# any non-virtual card rather than a fixed sleep.
_physical_card_present() {
    [ -r "$CARDS_FILE" ] || return 1
    grep -E '^[[:space:]]*[0-9]+[[:space:]]*\[' "$CARDS_FILE" 2>/dev/null \
        | grep -viE 'Loopback|vc4hdmi|UAC2' \
        | grep -q .
}

waited=0
while ! _physical_card_present; do
    if [ "$waited" -ge "$WAIT_SECONDS" ]; then
        log "WARNING: no physical sound card after ${WAIT_SECONDS}s — trying detection anyway"
        break
    fi
    sleep 1
    waited=$((waited + 1))
done

DETECT_OUTPUT="$("$SCRIPT_DIR/detect-jack-device.sh" 2>&1)"
DETECT_EXIT=$?

JACK_DEVICE="$(printf '%s\n' "$DETECT_OUTPUT" | grep '^JACK_DEVICE=' | cut -d= -f2-)"
JACK_CARD_ID="$(printf '%s\n' "$DETECT_OUTPUT" | grep '^JACK_CARD_ID=' | cut -d= -f2-)"
JACK_TIER="$(printf '%s\n' "$DETECT_OUTPUT" | grep '^TIER=' | cut -d= -f2-)"

if [ $DETECT_EXIT -ne 0 ] || [ -z "$JACK_DEVICE" ]; then
    log "ERROR: no ALSA card resolved for jackd — refusing to start the server"
    printf '%s\n' "$DETECT_OUTPUT" >&2
    exit 1
fi

{
    printf 'JACK_DEVICE=%s\n' "$JACK_DEVICE"
    printf 'JACK_CARD_ID=%s\n' "$JACK_CARD_ID"
    printf 'TIER=%s\n' "$JACK_TIER"
} >"$DEVICE_FILE"
chmod 0644 "$DEVICE_FILE" 2>/dev/null || true

log "device $JACK_DEVICE (card ${JACK_CARD_ID:-unknown}, tier ${JACK_TIER:-unknown}) after ${waited}s wait"
