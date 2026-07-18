#!/bin/bash
# Test audio device detection logic
# This script helps diagnose audio device detection without launching Surge

SURGE_CLI="${1:-/home/mitch/surge/build/surge_xt_products/surge-xt-cli}"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

echo "======================================="
echo "  Audio Device Detection Test"
echo "======================================="
echo ""

# Check if surge CLI exists
if [ ! -f "$SURGE_CLI" ]; then
    echo "❌ ERROR: Surge CLI not found at: $SURGE_CLI"
    echo ""
    echo "Usage: $0 [path-to-surge-xt-cli]"
    exit 1
fi

echo "✓ Surge CLI found: $SURGE_CLI"
echo ""

# Run detection script
echo "--- Testing Detection Script ---"
DETECTION_OUTPUT=$("$SCRIPT_DIR/detect-audio-device.sh" "$SURGE_CLI" 2>&1)
DETECTION_EXIT=$?

if [ $DETECTION_EXIT -eq 0 ]; then
    echo "✅ Detection successful!"
    echo ""
    echo "$DETECTION_OUTPUT" | grep -E "^(DEVICE_ID|DEVICE_NAME|TIER)="
    echo ""

    # Extract values for summary
    DEVICE_ID=$(echo "$DETECTION_OUTPUT" | grep "^DEVICE_ID=" | cut -d= -f2)
    DEVICE_NAME=$(echo "$DETECTION_OUTPUT" | grep "^DEVICE_NAME=" | cut -d= -f2)
    TIER=$(echo "$DETECTION_OUTPUT" | grep "^TIER=" | cut -d= -f2)
    REASON=$(echo "$DETECTION_OUTPUT" | grep "^REASON=" | cut -d= -f2)

    echo "📊 Summary:"
    echo "   Device ID: $DEVICE_ID"
    echo "   Device: $DEVICE_NAME"
    echo "   Tier: $TIER"
    if [ -n "$REASON" ]; then
        echo "   Reason: $REASON"
    fi
else
    echo "❌ Detection failed!"
    echo ""
    echo "$DETECTION_OUTPUT"
fi

echo ""
echo "--- All Available Output Devices (from Surge) ---"
"$SURGE_CLI" --list-devices 2>&1 | grep "Output Audio Device" | nl

echo ""
echo "--- ALSA Playback Devices ---"
if command -v aplay >/dev/null 2>&1; then
    aplay -l 2>/dev/null || echo "aplay command failed"
else
    echo "aplay not available"
fi

echo ""
echo "--- ALSA Cards ---"
if [ -f /proc/asound/cards ]; then
    cat /proc/asound/cards
else
    echo "/proc/asound/cards not found"
fi

echo ""
echo "======================================="
echo "  Test Complete"
echo "======================================="

exit $DETECTION_EXIT
