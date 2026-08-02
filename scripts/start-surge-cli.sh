#!/bin/bash
# Surge XT CLI - Headless startup script with robust audio device detection

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

echo "$(date): Starting Surge XT CLI..." >> "$LOG_FILE"

AUDIO_RESULT=$("$SCRIPT_DIR/detect-audio-device.sh" "$SURGE_CLI" 2>&1)
DETECTION_EXIT=$?

if [ $DETECTION_EXIT -ne 0 ]; then
    echo "$(date): CRITICAL - Audio detection failed completely" >> "$LOG_FILE"
    echo "$AUDIO_RESULT" >> "$LOG_FILE"
    exit 1
fi

AUDIO_DEVICE=$(echo "$AUDIO_RESULT" | grep "^DEVICE_ID=" | cut -d= -f2)
DEVICE_NAME=$(echo "$AUDIO_RESULT" | grep "^DEVICE_NAME=" | cut -d= -f2)
DEVICE_TIER=$(echo "$AUDIO_RESULT" | grep "^TIER=" | cut -d= -f2)

echo "$(date): Selected audio device: $AUDIO_DEVICE" >> "$LOG_FILE"
echo "$(date):   Name: $DEVICE_NAME" >> "$LOG_FILE"
echo "$(date):   Tier: $DEVICE_TIER" >> "$LOG_FILE"

USER_DEFAULTS_DIR="$(dirname "$MPE_SURGE_USER_DEFAULTS")"
USER_DEFAULTS="$MPE_SURGE_USER_DEFAULTS"
mkdir -p "$USER_DEFAULTS_DIR"

if [ -f "$USER_DEFAULTS" ]; then
    chmod 644 "$USER_DEFAULTS"
    echo "$(date): Set existing user defaults to writable (644) for OSC patch loading" >> "$LOG_FILE"
else
    cat > "$USER_DEFAULTS" << 'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<surge-xt-user-defaults>
</surge-xt-user-defaults>
XMLEOF
    chmod 644 "$USER_DEFAULTS"
    echo "$(date): Created minimal user defaults file for OSC patch loading" >> "$LOG_FILE"
fi

if [ -f "$SCRIPT_DIR/wait-for-usb-midi.sh" ]; then
    # shellcheck source=lib/profile-switch-flag.sh
    source "$SCRIPT_DIR/lib/profile-switch-flag.sh"
    if profile_switch_flag_set; then
        profile_switch_flag_clear
        echo "$(date): Skipping USB MIDI wait (audio profile switch restart)" >> "$LOG_FILE"
    else
        echo "$(date): Waiting for USB MIDI devices..." >> "$LOG_FILE"
        bash "$SCRIPT_DIR/wait-for-usb-midi.sh" >> "$LOG_FILE" 2>&1
    fi
fi

echo "$(date): USB devices at startup:" >> "$LOG_FILE"
lsusb 2>&1 | grep -i "midi\|roli\|seaboard" >> "$LOG_FILE" || echo "  No USB MIDI devices found" >> "$LOG_FILE"

# Drop idle ALSA loopback from calibration (extra PCM/timer overhead on the Pi).
# shellcheck source=lib/unload-snd-aloop.sh
source "$SCRIPT_DIR/lib/unload-snd-aloop.sh"

SURGE_BUFFER_SIZE="${MPE_SURGE_BUFFER_SIZE:-1024}"
echo "$(date): ALSA buffer size: $SURGE_BUFFER_SIZE samples" >> "$LOG_FILE"

"$SURGE_CLI" \
  --all-midi-inputs \
  --mpe-enable \
  --mpe-pitch-bend-range=48 \
  --audio-interface="$AUDIO_DEVICE" \
  --buffer-size="$SURGE_BUFFER_SIZE" \
  --osc-in-port=53280 \
  --osc-out-port=53270 \
  --no-stdin \
  >> "$LOG_FILE" 2>&1 &

SURGE_PID=$!
echo "$(date): Surge XT CLI started with PID $SURGE_PID (Audio device: $AUDIO_DEVICE)" >> "$LOG_FILE"
echo "Surge XT CLI running (PID: $SURGE_PID)"

sleep 2
