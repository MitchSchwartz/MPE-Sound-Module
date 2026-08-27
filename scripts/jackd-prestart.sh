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
#
# While waiting, publish reason=no-device so the UI can say "No audio device"
# instead of spinning "Reconnecting audio…" forever. restart-audio-graph.sh
# issues the restart with --no-block, so state=recovering is published
# optimistically and nothing reconciles it when this unit then fails to start.
# With nothing plugged in there is nothing to reconnect TO, and a progress
# spinner that can never finish is a lie about what the appliance is doing.
waited=0
announced_no_device=false
while ! mpe_physical_playback_card_present; do
    if [ "$announced_no_device" = false ]; then
        announced_no_device=true
        log "no physical sound card — waiting up to ${WAIT_SECONDS}s"
        mpe_engine_state_write "$MPE_ENGINE_NAME" none recovering no-device \
            "$(mpe_looper_state_label)" || true
    fi
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
    # Distinguish "nothing is plugged in" from "a device is present but unusable".
    # Both fail the unit; only the first is fixed by the user plugging something in.
    if mpe_physical_playback_card_present; then
        mpe_engine_state_write "$MPE_ENGINE_NAME" none failed no-card-resolved \
            "$(mpe_looper_state_label)" || true
    else
        mpe_engine_state_write "$MPE_ENGINE_NAME" none failed no-device \
            "$(mpe_looper_state_label)" || true
    fi
    exit 1
fi

{
    printf 'JACK_DEVICE=%s\n' "$JACK_DEVICE"
    printf 'JACK_CARD_ID=%s\n' "$JACK_CARD_ID"
    printf 'TIER=%s\n' "$JACK_TIER"
} >"$DEVICE_FILE"
chmod 0644 "$DEVICE_FILE" 2>/dev/null || true

log "device $JACK_DEVICE (card ${JACK_CARD_ID:-unknown}, tier ${JACK_TIER:-unknown}) after ${waited}s wait"

# Assert the interface can actually pass host audio BEFORE jackd binds it.
# A device in standalone mode, or with its outputs fed from its own hardware
# mixer, discards everything jackd writes while every other reading on the
# appliance stays green (2026-08-26). Never fails the unit: a guard that blocks
# startup would turn a recoverable misconfiguration into no instrument at all.
# shellcheck source=lib/interface-guard.sh
source "$SCRIPT_DIR/lib/interface-guard.sh"
mpe_interface_guard "$(printf '%s' "$JACK_DEVICE" | sed 's/^hw://;s/,.*//')" || true
