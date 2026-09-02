#!/bin/bash
# Audio device detection with multi-tier fallback
# Returns: DEVICE_ID, DEVICE_NAME, TIER
#
# Usage: detect-audio-device.sh [path-to-surge-xt-cli]
#
# Tier 0 (usb-host + host capturing): UAC2 gadget → tethered host PC
# Tier 1: Sound Blaster Play! 3 (standalone default; usb-host idle; usb-host-session always)
# Tier 2–4: generic USB, Pi headphone, last resort
#
# Exit codes:
#   0 - Success, device found
#   1 - Error, no devices available

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

SURGE_CLI="${1:-$SURGE_CLI}"
AUDIO_PROFILE="${MPE_AUDIO_PROFILE:-standalone}"

# Verify surge CLI exists
if [ ! -f "$SURGE_CLI" ]; then
    echo "ERROR: Surge CLI not found at: $SURGE_CLI" >&2
    exit 1
fi

# Get all output devices from surge — ignore a noisy non-zero exit when output
# is still present (finding 6; same pattern as resolve_jack_device_index).
DEVICE_LIST=$("$SURGE_CLI" --list-devices 2>&1 | grep "Output Audio Device" || true)

if [ -z "$DEVICE_LIST" ]; then
    echo "ERROR: No audio devices found by surge-xt-cli --list-devices" >&2
    exit 1
fi

extract_device_id() {
    local line="$1"
    echo "$line" | sed -n 's/.*\[\([0-9][0-9]*\.[0-9][0-9]*\)\].*/\1/p'
}

get_device_name() {
    local device_id="$1"
    echo "$DEVICE_LIST" | grep "\[$device_id\]" | sed 's/.*\] : //' | sed 's/;.*//' | head -1 || true
}

GADGET_GREP='UAC2[_ ]?Gadget|UAC2Gadget|USB Audio Passthrough|MPE Sound Module|ALSA\.UAC2'
# Virtual sinks in JUCE's device-string namespace. Keep in step with
# mpe_card_is_virtual() in lib/audio-engine.sh — same policy, different spelling.
VIRTUAL_GREP='ALSA\.Dummy|Dummy PCM|ALSA\.Loopback|Loopback PCM'

filter_gadget_devices() {
    echo "$DEVICE_LIST" | grep -iE "$GADGET_GREP" || true
}

try_select_uac2_gadget() {
    local reason="${1:?reason required}"
    local gadget_devices device device_id device_name
    gadget_devices=$(filter_gadget_devices)
    device=$(echo "$gadget_devices" | grep -i "Direct hardware" | head -1 || true)
    if [ -z "$device" ]; then
        device=$(echo "$gadget_devices" | grep -v "Direct sample mixing" | head -1 || true)
    fi
    if [ -z "$device" ]; then
        return 1
    fi
    device_id=$(extract_device_id "$device")
    [ -n "$device_id" ] || return 1
    device_name=$(get_device_name "$device_id")
    echo "DEVICE_ID=$device_id"
    echo "DEVICE_NAME=$device_name"
    echo "TIER=0"
    echo "REASON=$reason" >&2
    return 0
}

# ============================================================================
# TIER 0: UAC2 gadget — only while host capture is active (usb-host profile only)
# usb-host-session keeps Surge on Sound Blaster; mic bridge feeds the gadget.
# ============================================================================
if [ "$AUDIO_PROFILE" = "usb-host" ]; then
    # shellcheck source=lib/uac2-host-route.sh
    source "$SCRIPT_DIR/lib/uac2-host-route.sh"
    if uac2_host_streaming_active; then
        if try_select_uac2_gadget "USB audio gadget (host capture active)"; then
            exit 0
        fi
        echo "REASON=host capturing but no UAC2 gadget — falling back to idle output" >&2
    else
        echo "REASON=usb-host idle — local output until host opens capture" >&2
    fi
elif [ "$AUDIO_PROFILE" = "usb-host-session" ]; then
    echo "REASON=usb-host-session — Surge on Sound Blaster; mic→gadget when host captures" >&2
fi

# ============================================================================
# TIER 1: Preferred USB DAC (Sound Blaster Play! 3)
# ============================================================================
DEVICE=$(echo "$DEVICE_LIST" | \
    grep "Sound Blaster Play! 3" | \
    grep "Front output" | \
    head -1 || true)

if [ -z "$DEVICE" ]; then
    DEVICE=$(echo "$DEVICE_LIST" | \
        grep "Sound Blaster Play! 3" | \
        grep -v "Surround" | \
        grep -v "S/PDIF" | \
        grep -v "USB Stream" | \
        grep -v "Direct hardware" | \
        grep -v "Direct sample mixing" | \
        head -1 || true)
fi

if [ -n "$DEVICE" ]; then
    DEVICE_ID=$(extract_device_id "$DEVICE")
    if [ -n "$DEVICE_ID" ]; then
        DEVICE_NAME=$(get_device_name "$DEVICE_ID")
        echo "DEVICE_ID=$DEVICE_ID"
        echo "DEVICE_NAME=$DEVICE_NAME"
        echo "TIER=1"
        echo "REASON=Preferred USB DAC (Sound Blaster Play! 3)" >&2
        exit 0
    fi
fi

# ============================================================================
# TIER 2: Any USB audio device — including the usb-host IDLE sink.
#
# This used to be skipped entirely under usb-host. The reason was sound: the
# match below is a bare "usb", which happily selects the UAC2 gadget itself,
# and binding the gadget while the host is not capturing is the stall this
# whole profile is built to avoid. But skipping the tier threw out every other
# USB DAC with it.
#
# The appliance's interface is a Focusrite Scarlett 4i4, which only ever
# resolves here — tier 1 matches the reference Sound Blaster by product name.
# So under usb-host there was no idle sink at all: tier 3 then matched
# vc4-hdmi, and jackd was handed a card that cannot play. The USB route never
# came up either, because the host-route watcher starts from
# surge-xt-cli.service ExecStartPost and Surge never started.
#
# Excluding the gadget keeps the original reason for the skip and restores the
# idle sink. See docs/USB-AUDIO-HOST.md for the idle/active table.
# ============================================================================
# VIRTUAL_GREP mirrors mpe_card_is_virtual() for the JUCE *device-string*
# namespace (this script matches "ALSA.Dummy, Dummy PCM"; the predicate matches
# ALSA card ids). Two namespaces, one policy — kept adjacent and named so the
# next card type is added to both, which is precisely what did not happen when
# snd-dummy landed.
DEVICE=$(echo "$DEVICE_LIST" | \
    grep -i "usb" | \
    grep -viE "$GADGET_GREP" | \
    grep -viE "$VIRTUAL_GREP" | \
    grep -v "Surround" | \
    grep -v "S/PDIF" | \
    grep -vi "HDMI" | \
    grep -v "Stream" | \
    head -1 || true)

if [ -n "$DEVICE" ]; then
    DEVICE_ID=$(extract_device_id "$DEVICE")
    if [ -n "$DEVICE_ID" ]; then
        DEVICE_NAME=$(get_device_name "$DEVICE_ID")
        echo "DEVICE_ID=$DEVICE_ID"
        echo "DEVICE_NAME=$DEVICE_NAME"
        echo "TIER=2"
        if [ "$AUDIO_PROFILE" = "usb-host" ]; then
            echo "REASON=usb-host idle sink (USB DAC — host capture not active)" >&2
        else
            echo "REASON=Generic USB audio device found" >&2
        fi
        exit 0
    fi
fi

# ============================================================================
# TIER 3: the IDLE SINK — a free-running local clock while the host is quiet
# ============================================================================
# `grep -v "HDMI"` was case-SENSITIVE, and JUCE reports the Pi's HDMI outputs
# as "ALSA.vc4-hdmi-0" in lower case — so the exclusion never fired and this
# "headphone jack" tier returned an HDMI port. The Pi 5 has no headphone jack
# at all, so on that board this tier must find nothing and fall through.
#
# WHY THE LOOPBACK IS HERE (2026-08-30). `usb-host` deliberately refuses to
# bind the UAC2 gadget until the host is actually capturing, and that refusal
# is not a policy — MEASURED on the appliance the same day:
#
#     aplay -D hw:2,0 (gadget), host not capturing -> EIO after 1s
#
# A UAC2 gadget has no clock of its own. Under the USB Audio Class spec the
# HOST enables the streaming interface, and isochronous transfers happen only
# while it is active; until then the endpoint rejects writes. So "bind early
# and let the host start whenever" cannot work at the ALSA layer, and the
# standard arrangement — the one hardware synths with USB audio out use — is to
# run the engine on a free-running local clock and bridge into the gadget when
# the host appears. `usb-host-session` is that arrangement.
#
# It needs a local clock, and docs/USB-AUDIO-HOST.md supplies the Pi 4 answer:
# "No external DAC: idle sink is Pi headphone". The Pi 5 has no headphone jack
# and its HDMI ports read `disconnected` with no display, so on a Pi 5 with no
# external DAC there was NO idle sink at all — jackd could not start, Surge
# could not start, and the appliance was silent with a misleading error. That
# is a hardware-generation assumption that outlived its board.
#
# snd-dummy free-runs off its own hrtimer and needs no reader, which is exactly
# the property the headphone jack was providing. NOT snd-aloop: that is a pipe,
# whose playback side only advances while something reads its capture side, so
# jackd's driver thread dies with "ALSA: poll time out ... Exiting". An `aplay`
# to it still completes in real time, which looks identical to a working clock
# and is not one. It ranks BELOW every real DAC
# (tiers 1 and 2) so a plugged-in interface always wins, and above the
# last-resort tier so "no sink at all" still fails loudly.
DEVICE=$(echo "$DEVICE_LIST" | \
    grep -E "(Headphones|bcm2835)" | \
    grep -vi "hdmi" | \
    head -1 || true)

if [ -z "$DEVICE" ]; then
    DEVICE=$(echo "$DEVICE_LIST" | grep -iE 'Dummy' | head -1 || true)
    [ -n "$DEVICE" ] && IDLE_SINK_KIND="snd-dummy"
fi

if [ -n "$DEVICE" ]; then
    DEVICE_ID=$(extract_device_id "$DEVICE")
    if [ -n "$DEVICE_ID" ]; then
        DEVICE_NAME=$(get_device_name "$DEVICE_ID")
        echo "DEVICE_ID=$DEVICE_ID"
        echo "DEVICE_NAME=$DEVICE_NAME"
        echo "TIER=3"
        _sink_kind="${IDLE_SINK_KIND:-Pi headphone}"
        if [ "$AUDIO_PROFILE" = "usb-host" ]; then
            echo "REASON=usb-host idle sink ($_sink_kind — host capture not active)" >&2
        else
            echo "REASON=$_sink_kind (fallback)" >&2
        fi
        exit 0
    fi
fi

# ============================================================================
# TIER 4: First available output device (last resort)
#
# The gadget is excluded here as well. Reaching it by accident — rather than
# through tier 0, which first confirms the host is capturing — binds a PCM
# nobody is draining, and that is the stall this profile exists to prevent.
# With genuinely nothing else present, failing loudly beats wedging quietly.
# ============================================================================
# ...and so are ALSA's virtual/plug entries. This tier returned
# "ALSA.Default Audio Device (1)" on 2026-08-30 with genuinely nothing plugged
# in. That is not a card: `jackd-prestart.sh` could not map it to anything in
# /proc/asound/cards and failed one layer later with "no ALSA card matches tier
# '4'" — an error that sent a diagnosis hunting for a missing sound interface
# instead of reporting the truth, which was that no sink existed at all. This
# tier's own comment already promised to fail loudly; now it does.
DEVICE=$(echo "$DEVICE_LIST" \
    | grep -viE "$GADGET_GREP" \
    | grep -viE 'Default Audio Device|Dummy' \
    | head -1 || true)

if [ -n "$DEVICE" ]; then
    DEVICE_ID=$(extract_device_id "$DEVICE")
    if [ -n "$DEVICE_ID" ]; then
        DEVICE_NAME=$(get_device_name "$DEVICE_ID")
        echo "DEVICE_ID=$DEVICE_ID"
        echo "DEVICE_NAME=$DEVICE_NAME"
        echo "TIER=4"
        echo "REASON=First available audio device (last resort)" >&2
        exit 0
    fi
fi

echo "ERROR: Could not detect any valid audio device" >&2
echo "Available devices:" >&2
echo "$DEVICE_LIST" >&2
exit 1
