#!/bin/bash
# Surge XT CLI - Headless startup script with robust audio device detection
# Uses multi-tier fallback: USB DAC -> Pi headphone jack -> any available device

SURGE_CLI="/home/mitch/surge/build/surge_xt_products/surge-xt-cli"
# INIT_PATCH="/home/mitch/surge/resources/data/patches_factory/Keys/Church.fxp" # Disabled - causes user defaults corruption
LOG_FILE="/home/mitch/surge-cli.log"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

echo "$(date): Starting Surge XT CLI..." >> "$LOG_FILE"

# Use robust audio detection script with 4-tier fallback
AUDIO_RESULT=$("$SCRIPT_DIR/detect-audio-device.sh" "$SURGE_CLI" 2>&1)
DETECTION_EXIT=$?

if [ $DETECTION_EXIT -ne 0 ]; then
    echo "$(date): CRITICAL - Audio detection failed completely" >> "$LOG_FILE"
    echo "$AUDIO_RESULT" >> "$LOG_FILE"
    exit 1
fi

# Parse detection results
AUDIO_DEVICE=$(echo "$AUDIO_RESULT" | grep "^DEVICE_ID=" | cut -d= -f2)
DEVICE_NAME=$(echo "$AUDIO_RESULT" | grep "^DEVICE_NAME=" | cut -d= -f2)
DEVICE_TIER=$(echo "$AUDIO_RESULT" | grep "^TIER=" | cut -d= -f2)

# Log selection with detailed information
echo "$(date): Selected audio device: $AUDIO_DEVICE" >> "$LOG_FILE"
echo "$(date):   Name: $DEVICE_NAME" >> "$LOG_FILE"
echo "$(date):   Tier: $DEVICE_TIER" >> "$LOG_FILE"

# Ensure user defaults directory exists
USER_DEFAULTS_DIR="$HOME/.local/share/Surge XT"
USER_DEFAULTS="$USER_DEFAULTS_DIR/SurgeXTUserDefaults.xml"
mkdir -p "$USER_DEFAULTS_DIR"

# Make user defaults writable (OSC patch loading requires write access)
# Note: Read-only protection (chmod 444) breaks OSC /patch/load commands
# CLI mode with OSC enabled MUST have writable user defaults
if [ -f "$USER_DEFAULTS" ]; then
    chmod 644 "$USER_DEFAULTS"
    echo "$(date): Set existing user defaults to writable (644) for OSC patch loading" >> "$LOG_FILE"
else
    # Create minimal valid XML file to prevent Surge crashes on patch load
    cat > "$USER_DEFAULTS" << 'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<surge-xt-user-defaults>
</surge-xt-user-defaults>
XMLEOF
    chmod 644 "$USER_DEFAULTS"
    echo "$(date): Created minimal user defaults file for OSC patch loading" >> "$LOG_FILE"
fi

# Wait for USB MIDI devices to enumerate (helps avoid race condition on boot)
if [ -f "$SCRIPT_DIR/wait-for-usb-midi.sh" ]; then
    echo "$(date): Waiting for USB MIDI devices..." >> "$LOG_FILE"
    bash "$SCRIPT_DIR/wait-for-usb-midi.sh" >> "$LOG_FILE" 2>&1
fi

# Log MIDI device state for diagnostics
echo "$(date): USB devices at startup:" >> "$LOG_FILE"
lsusb 2>&1 | grep -i "midi\|roli\|seaboard" >> "$LOG_FILE" || echo "  No USB MIDI devices found" >> "$LOG_FILE"

"$SURGE_CLI" \
  --all-midi-inputs \
  --mpe-enable \
  --mpe-pitch-bend-range=48 \
  --audio-interface="$AUDIO_DEVICE" \
  --osc-in-port=53280 \
  --no-stdin \
  >> "$LOG_FILE" 2>&1 &

SURGE_PID=$!
echo "$(date): Surge XT CLI started with PID $SURGE_PID (Audio device: $AUDIO_DEVICE)" >> "$LOG_FILE"
echo "Surge XT CLI running (PID: $SURGE_PID)"

# Wait for Surge to initialize OSC port
sleep 2

# Volume is at default (100%) - no adjustment needed
