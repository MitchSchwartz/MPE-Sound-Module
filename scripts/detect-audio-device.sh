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
# TIER 2: Any USB audio device (standalone only — skip in usb-host idle)
# ============================================================================
if [ "$AUDIO_PROFILE" != "usb-host" ] && [ "$AUDIO_PROFILE" != "usb-host-session" ]; then
DEVICE=$(echo "$DEVICE_LIST" | \
    grep -i "usb" | \
    grep -v "Surround" | \
    grep -v "S/PDIF" | \
    grep -v "HDMI" | \
    grep -v "Stream" | \
    head -1 || true)

if [ -n "$DEVICE" ]; then
    DEVICE_ID=$(extract_device_id "$DEVICE")
    if [ -n "$DEVICE_ID" ]; then
        DEVICE_NAME=$(get_device_name "$DEVICE_ID")
        echo "DEVICE_ID=$DEVICE_ID"
        echo "DEVICE_NAME=$DEVICE_NAME"
        echo "TIER=2"
        echo "REASON=Generic USB audio device found" >&2
        exit 0
    fi
fi
fi

# ============================================================================
# TIER 3: Pi headphone jack — idle sink for usb-host without Sound Blaster
# ============================================================================
DEVICE=$(echo "$DEVICE_LIST" | \
    grep -E "(Headphones|bcm2835|vc4-hdmi)" | \
    grep -v "HDMI" | \
    head -1 || true)

if [ -n "$DEVICE" ]; then
    DEVICE_ID=$(extract_device_id "$DEVICE")
    if [ -n "$DEVICE_ID" ]; then
        DEVICE_NAME=$(get_device_name "$DEVICE_ID")
        echo "DEVICE_ID=$DEVICE_ID"
        echo "DEVICE_NAME=$DEVICE_NAME"
        echo "TIER=3"
        if [ "$AUDIO_PROFILE" = "usb-host" ]; then
            echo "REASON=usb-host idle sink (Pi headphone — host capture not active)" >&2
        else
            echo "REASON=Raspberry Pi headphone jack (fallback)" >&2
        fi
        exit 0
    fi
fi

# ============================================================================
# TIER 4: First available output device (last resort)
# ============================================================================
DEVICE=$(echo "$DEVICE_LIST" | head -1)

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
