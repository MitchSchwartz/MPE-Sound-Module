#!/bin/bash
# Resolve the ALSA card jackd should bind, using the SAME tier logic as Surge.
#
# Spec D1: jackd owns the hardware, so the existing tier policy in
# detect-audio-device.sh picks jackd's `-d hw:N` instead of Surge's
# --audio-interface. This script does not reimplement the tiers — it calls that
# script and translates its answer (a JUCE device index plus a device name) into
# an ALSA card number, which is the only thing jackd understands.
#
# Usage: detect-jack-device.sh [path-to-surge-xt-cli]
#
# Output (stdout):
#   JACK_DEVICE=hw:1
#   JACK_CARD_ID=Play3
#   TIER=1
#
# Exit codes:
#   0 - card resolved
#   1 - no usable card (message on stderr names the tier that was requested)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

SURGE_CLI="${1:-$SURGE_CLI}"
CARDS_FILE="${MPE_ASOUND_CARDS:-/proc/asound/cards}"

if [ ! -r "$CARDS_FILE" ]; then
    echo "ERROR: cannot read $CARDS_FILE — no ALSA cards visible" >&2
    exit 1
fi

# The looper's snd-aloop tier is meaningless for jackd: under JACK there is no
# loopback capture path (the looper is guarded off until spec Phase 2), so the
# server must bind the real output device.
DETECT_OUTPUT="$(MPE_LOOPER_ENABLED=0 "$SCRIPT_DIR/detect-audio-device.sh" "$SURGE_CLI" 2>/dev/null)"
DETECT_EXIT=$?

if [ $DETECT_EXIT -ne 0 ]; then
    echo "ERROR: tier detection failed — cannot choose a card for jackd" >&2
    exit 1
fi

DEVICE_NAME="$(printf '%s\n' "$DETECT_OUTPUT" | grep '^DEVICE_NAME=' | cut -d= -f2-)"
TIER="$(printf '%s\n' "$DETECT_OUTPUT" | grep '^TIER=' | cut -d= -f2-)"

# One record per card: index|id|description (continuation lines folded in).
_card_records() {
    awk '
    BEGIN { OFS = "|"; idx = "" }
    /^[[:space:]]*[0-9]+[[:space:]]*\[/ {
        if (idx != "") { print idx, id, desc }
        idx = $1
        id = $0
        sub(/^[^[]*\[/, "", id)
        sub(/\].*$/, "", id)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", id)
        desc = $0
        sub(/^[^]]*\][[:space:]]*:[[:space:]]*/, "", desc)
        next
    }
    {
        line = $0
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", line)
        if (idx != "" && line != "") { desc = desc " " line }
    }
    END { if (idx != "") { print idx, id, desc } }
    ' "$CARDS_FILE"
}

# Distinctive part of the JUCE device name: "Front output on Sound Blaster Play! 3"
# and "Direct hardware device on ALSA.UAC2_Gadget" both carry the hardware name
# after the last " on ".
_device_name_hint() {
    local name="$DEVICE_NAME"
    case "$name" in
        *" on "*) name="${name##* on }" ;;
    esac
    name="${name#ALSA.}"
    printf '%s' "$name"
}

_records="$(_card_records)"

if [ -z "$_records" ]; then
    echo "ERROR: no ALSA cards listed in $CARDS_FILE" >&2
    exit 1
fi

_emit() {
    local record="$1"
    local reason="$2"
    local idx id
    idx="$(printf '%s' "$record" | cut -d'|' -f1)"
    id="$(printf '%s' "$record" | cut -d'|' -f2)"
    echo "JACK_DEVICE=hw:$idx"
    echo "JACK_CARD_ID=$id"
    echo "TIER=$TIER"
    echo "REASON=$reason" >&2
    exit 0
}

# 1. Name match — the tier already decided *which* hardware; trust its answer.
_hint="$(_device_name_hint)"
if [ -n "$_hint" ]; then
    _match="$(printf '%s\n' "$_records" | grep -iF -- "$_hint" | head -1)"
    if [ -n "$_match" ]; then
        _emit "$_match" "tier $TIER device name match ($_hint)"
    fi
fi

# 2. Tier fallback — the name string may not appear verbatim in /proc/asound/cards.
case "$TIER" in
    0)
        _match="$(printf '%s\n' "$_records" | grep -iE 'UAC2' | head -1)"
        ;;
    1)
        _match="$(printf '%s\n' "$_records" | grep -iF 'Sound Blaster' | head -1)"
        ;;
    2)
        _match="$(printf '%s\n' "$_records" | grep -iE 'USB-?Audio' \
            | grep -viE 'UAC2|Loopback' | head -1)"
        ;;
    3)
        _match="$(printf '%s\n' "$_records" | grep -iE 'Headphones|bcm2835' \
            | grep -vi 'HDMI' | head -1)"
        ;;
    *)
        _match=""
        ;;
esac

if [ -n "$_match" ]; then
    _emit "$_match" "tier $TIER card pattern match"
fi

# 3. Last resort — first non-virtual card, mirroring detect-audio-device.sh TIER 4.
_match="$(printf '%s\n' "$_records" | grep -viE 'Loopback|vc4hdmi|UAC2' | head -1)"
if [ -n "$_match" ]; then
    _emit "$_match" "no tier $TIER card — first physical card (last resort)"
fi

echo "ERROR: no ALSA card matches tier '$TIER' (device name: ${DEVICE_NAME:-unknown})" >&2
echo "Cards seen:" >&2
printf '%s\n' "$_records" >&2
exit 1
