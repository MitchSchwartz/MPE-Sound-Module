#!/bin/bash
# Wait for Roli controller USB + ALSA MIDI port before opening RtMidi.
#
# lsusb alone is insufficient: the LUMI can enumerate on USB while the ALSA
# client (card 6 / client 40) is still absent — that mismatch caused hundreds
# of mpe-pressure-remap restart cycles reporting "Roli not detected".

TIMEOUT=15
ROLI_VID="2af4"
MAX_CHECKS=30  # 30 * 0.5s = 15s
STABLE_REQUIRED=3

roli_usb_present() {
    lsusb 2>/dev/null | grep -qi "${ROLI_VID}:"
}

roli_midi_port_present() {
    aconnect -l 2>/dev/null | grep -qiE 'lumi|seaboard|roli'
}

roli_ready() {
    roli_usb_present && roli_midi_port_present
}

# Fast path: USB + ALSA MIDI stable
stable_count=0
for _ in $(seq 1 3); do
    if roli_ready; then
        stable_count=$((stable_count + 1))
    else
        stable_count=0
    fi
    sleep 0.2
done

if [ "$stable_count" -ge "$STABLE_REQUIRED" ]; then
    echo "$(date): Roli controller + ALSA MIDI port already stable"
    exit 0
fi

stable_count=0
for i in $(seq 1 $MAX_CHECKS); do
    if roli_ready; then
        stable_count=$((stable_count + 1))
        if [ "$stable_count" -ge "$STABLE_REQUIRED" ]; then
            echo "$(date): Roli USB + ALSA MIDI stable after $((i * 500))ms"
            sleep 0.5
            exit 0
        fi
    else
        stable_count=0
    fi
    sleep 0.5
done

if roli_usb_present && ! roli_midi_port_present; then
    echo "$(date): WARNING - Roli USB present but no ALSA MIDI port after ${TIMEOUT}s"
else
    echo "$(date): WARNING - Roli not detected after ${TIMEOUT}s"
fi
exit 0  # Non-blocking for callers that proceed anyway; gate script uses --required
