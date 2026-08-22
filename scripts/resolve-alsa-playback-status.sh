#!/bin/bash
# Resolve live ALSA playback status path for the tier-selected JACK device.
# Usage: ./scripts/resolve-alsa-playback-status.sh
# Prints: CARD=N STATUS=/proc/asound/cardN/pcm0p/sub0/status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"

if ! DETECT="$("$SCRIPT_DIR/detect-jack-device.sh" 2>/dev/null)"; then
    echo "ERROR: detect-jack-device failed" >&2
    exit 1
fi

CARD="$(printf '%s\n' "$DETECT" | awk -F= '/^JACK_DEVICE=/{gsub(/hw:/,"",$2); print $2}')"
if [ -z "$CARD" ]; then
    echo "ERROR: could not parse JACK_DEVICE from detect output" >&2
    exit 1
fi

STATUS="/proc/asound/card${CARD}/pcm0p/sub0/status"
if [ ! -r "$STATUS" ]; then
    echo "ERROR: status not readable: $STATUS (jackd may be down)" >&2
    exit 1
fi

printf 'CARD=%s\n' "$CARD"
printf 'STATUS=%s\n' "$STATUS"
printf 'JACK_CARD_ID=%s\n' "$(printf '%s\n' "$DETECT" | awk -F= '/^JACK_CARD_ID=/{print $2}')"
