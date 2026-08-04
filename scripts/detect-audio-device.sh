#!/bin/bash
# Audio device detection with multi-tier fallback
# Returns: DEVICE_ID, DEVICE_NAME, TIER
#
# Usage: detect-audio-device.sh [path-to-surge-xt-cli]
#
# Tier 0 (usb-host profile): UAC2 gadget → tethered host PC
# Tier 1: Sound Blaster Play! 3 (standalone default)
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

# Get all output devices from surge
DEVICE_LIST=$("$SURGE_CLI" --list-devices 2>&1 | grep "Output Audio Device" || true)

if [ -z "$DEVICE_LIST" ]; then
    echo "ERROR: No audio devices found by surge-xt-cli --list-devices" >&2
    exit 1
fi

# Function to extract device ID from device list line
extract_device_id() {
    local line="$1"
    echo "$line" | sed -n 's/.*\[\([0-9][0-9]*\.[0-9][0-9]*\)\].*/\1/p'
}

# Function to get device name from device list by ID
get_device_name() {
    local device_id="$1"
    # Extract everything after the ] and before the first ;
    echo "$DEVICE_LIST" | grep "\[$device_id\]" | sed 's/.*\] : //' | sed 's/;.*//' | head -1 || true
}

# Surge ALSA gadget lines (card id UAC2Gadget → ALSA.UAC2_Gadget; host USB names vary)
GADGET_GREP='UAC2[_ ]?Gadget|UAC2Gadget|USB Audio Passthrough|MPE Sound Module|ALSA\.UAC2'

# Filter DEVICE_LIST to lines that look like the configfs UAC2 gadget card
filter_gadget_devices() {
    echo "$DEVICE_LIST" | grep -iE "$GADGET_GREP" || true
}

# ============================================================================
# TIER 0: USB audio gadget (usb-host profile only)
# ============================================================================
if [ "$AUDIO_PROFILE" = "usb-host" ]; then
    # shellcheck source=lib/uac2-lazy-route.sh
    source "$SCRIPT_DIR/lib/uac2-lazy-route.sh"

    use_gadget=0
    if uac2_force_output_active; then
        use_gadget=1
        uac2_force_output_clear
    elif ! uac2_lazy_route_enabled; then
        use_gadget=1
    fi

    if [ "$use_gadget" -eq 1 ]; then
        GADGET_DEVICES=$(filter_gadget_devices)

        # Prefer Direct hardware on the gadget card (e.g. [0.13] ALSA.UAC2_Gadget)
        DEVICE=$(echo "$GADGET_DEVICES" | grep -i "Direct hardware" | head -1 || true)

        # Fallback: any gadget line except Direct sample mixing
        if [ -z "$DEVICE" ]; then
            DEVICE=$(echo "$GADGET_DEVICES" | grep -v "Direct sample mixing" | head -1 || true)
        fi

        if [ -n "$DEVICE" ]; then
            DEVICE_ID=$(extract_device_id "$DEVICE")
            if [ -n "$DEVICE_ID" ]; then
                DEVICE_NAME=$(get_device_name "$DEVICE_ID")
                echo "DEVICE_ID=$DEVICE_ID"
                echo "DEVICE_NAME=$DEVICE_NAME"
                echo "TIER=0"
                echo "REASON=USB audio gadget (host passthrough, usb-host profile)" >&2
                exit 0
            fi
        fi
        echo "REASON=usb-host profile set but no gadget ALSA device found — falling back" >&2
    else
        echo "REASON=usb-host lazy route — Sound Blaster until host opens capture" >&2
    fi
fi

# ============================================================================
# TIER 1: Preferred USB DAC (Sound Blaster Play! 3)
# ============================================================================
# Look for "Front output" specifically, not "Direct hardware device"
DEVICE=$(echo "$DEVICE_LIST" | \
    grep "Sound Blaster Play! 3" | \
    grep "Front output" | \
    head -1 || true)

# If no "Front output", try excluding problematic variants
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
# TIER 2: Any USB audio device
# ============================================================================
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

# ============================================================================
# TIER 3: Raspberry Pi headphone jack (built-in audio)
# ============================================================================
# Look for bcm2835 (Pi's audio chip) or "Headphones" device
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
        echo "REASON=Raspberry Pi headphone jack (fallback)" >&2
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

# ============================================================================
# ABSOLUTE FAILURE - No valid audio device found
# ============================================================================
echo "ERROR: Could not detect any valid audio device" >&2
echo "Available devices:" >&2
echo "$DEVICE_LIST" >&2
exit 1
