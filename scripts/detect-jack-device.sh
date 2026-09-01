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
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

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
    name="${name%%, USB Audio*}"
    name="${name%%;*}"
    printf '%s' "$name"
}

_records="$(_card_records)"

if [ -z "$_records" ]; then
    echo "ERROR: no ALSA cards listed in $CARDS_FILE" >&2
    exit 1
fi

# Drop records whose card id is virtual, using the shared predicate. Every tier
# that needs this calls it -- a local regex here is how snd-dummy got through.
_drop_virtual_records() {
    local record id
    while IFS= read -r record; do
        [ -n "$record" ] || continue
        id="$(printf '%s' "$record" | cut -d'|' -f2)"
        mpe_card_is_virtual "$id" && continue
        printf '%s\n' "$record"
    done
}

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
            | _drop_virtual_records | head -1)"
        ;;
    3)
        # Headphones|bcm2835 is the Pi 4 idle sink. The Pi 5 has no headphone
        # jack, so there its idle sink is the snd-dummy card installed by
        # scripts/install-idle-sink.sh -- see docs/USB-AUDIO-HOST.md. It stays
        # OUT of the last-resort match below: picking a virtual
        # card by accident is exactly what that exclusion exists to prevent.
        # Here it is not an accident, it is what tier 3 asked for.
        _match="$(printf '%s\n' "$_records" | grep -iE 'Headphones|bcm2835|Dummy' \
            | grep -vi 'HDMI' | head -1)"
        ;;
    *)
        _match=""
        ;;
esac

if [ -n "$_match" ]; then
    _emit "$_match" "tier $TIER card pattern match"
fi

# A card can be "physical" and still have nothing to play out of. The APC mini
# is a control surface: it enumerates as USB-Audio and appears in
# /proc/asound/cards like any interface, but exposes no playback PCM at all.
# Handing it to jackd produces "ALSA: Cannot open PCM device alsa_pcm for
# playback", the server dies, Surge dies with it, and the appliance goes silent
# (measured 2026-08-28). A name blocklist cannot see this; the pcm nodes can.
_card_can_play() {
    local idx="$1"
    local root="${MPE_ASOUND_ROOT:-/proc/asound}"
    # No proc tree to consult (hermetic tests): do not veto on missing evidence.
    [ -d "$root/card$idx" ] || return 0
    ls "$root/card$idx" 2>/dev/null | grep -qE '^pcm[0-9]+p$'
}

# Virtual cards are excluded via the shared predicate in lib/audio-engine.sh, not
# a local regex. This list used to be one of five that had to agree by hand; when
# snd-dummy arrived only two of the five were updated.
_playable_records() {
    local record idx
    printf '%s\n' "$_records" | _drop_virtual_records | while IFS= read -r record; do
        [ -n "$record" ] || continue
        idx="$(printf '%s' "$record" | cut -d'|' -f1)"
        if _card_can_play "$idx"; then
            printf '%s\n' "$record"
        fi
    done
}

# 3. Last resort — first non-virtual card that can actually play.
_match="$(_playable_records | head -1)"
if [ -n "$_match" ]; then
    _emit "$_match" "no tier $TIER card — first playback-capable card (last resort)"
fi

echo "ERROR: no ALSA card matches tier '$TIER' (device name: ${DEVICE_NAME:-unknown})" >&2
echo "Cards seen:" >&2
printf '%s\n' "$_records" >&2
exit 1
