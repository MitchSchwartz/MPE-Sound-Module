#!/bin/bash
# Surge XT CLI - Headless startup script
#
# JACK is the only audio engine (spec Documents/specs/jack-audio-engine-spec.md,
# amended 2026-08-13 — ALSA removed entirely as a product audio path). Surge is
# always started as a JACK client; jackd owns the device and assigns the client
# audio thread its priority.
#
# "Never boot silent" is retired as a design goal. If the graph server is not
# accepting clients within a bounded wait, this script publishes state=failed
# and exits non-zero — loud and legible in the journal, the HUD, and
# `mpe engine status` — instead of opening the device directly on an alternate
# route. Restart=on-failure (surge-xt-cli.service) retries independently, and
# the supervisor promotes Surge onto the graph once jackd recovers.

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

echo "$(date): Starting Surge XT CLI..." >> "$LOG_FILE"

# Engine decisions must be visible in `journalctl -u surge-xt-cli`, not only in
# the private log file — a failure is the thing you go looking for at a gig.
engine_log() {
    echo "$(date): $1" >> "$LOG_FILE"
    echo "$1"
}

engine_log "Audio engine: jack (only engine — ALSA removed 2026-08-13)"

AUDIO_DEVICE=""

# JUCE exposes the graph as an audio device of type JACK; the index only exists
# while a server is up. Anchor on the device-listing prefix — libjack's own
# diagnostics also mention "Jack".
resolve_jack_device_index() {
    local list index
    # Surge may exit non-zero while still printing a usable device list — do not
    # treat a noisy exit as "no JACK device" (finding 6).
    list="$(timeout 5 "$SURGE_CLI" --list-devices 2>&1)" || true
    index=$(printf '%s\n' "$list" \
        | grep "Output Audio Device" \
        | grep -i "JACK" \
        | sed -n 's/.*\[\([0-9][0-9]*\.[0-9][0-9]*\)\].*/\1/p' \
        | head -1)
    [ -n "$index" ] || return 1
    printf '%s' "$index"
}

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

# Drop idle ALSA loopback from calibration. snd-aloop loading for looper routing
# is deferred to yolo/looper-phase0 — loading it here adds a Loopback card that
# detect-audio-device.sh must filter and has no consumer on this branch.
# shellcheck source=lib/unload-snd-aloop.sh
source "$SCRIPT_DIR/lib/unload-snd-aloop.sh"

SURGE_SAMPLE_RATE="${MPE_SURGE_SAMPLE_RATE:-48000}"

MPE_PRESSURE_REMAP="${MPE_PRESSURE_REMAP:-1}"
if [ "$MPE_PRESSURE_REMAP" = "1" ]; then
  # Remapper writes remapped LUMI → ALSA "Midi Through"; Surge reads that port only
  # (avoids JUCE failing to subscribe to RtMidi virtual ports).
  MPE_THROUGH_MIDI_INDEX="$(
    "$SURGE_CLI" --list-devices 2>&1 \
      | grep -i "Midi Through Port-0" \
      | sed -n 's/.*\[\([0-9][0-9]*\)\].*/\1/p' \
      | head -1
  )"
  if [ -n "$MPE_THROUGH_MIDI_INDEX" ]; then
    SURGE_MIDI_ARGS=(--midi-input="$MPE_THROUGH_MIDI_INDEX")
    echo "$(date): Surge MIDI input index $MPE_THROUGH_MIDI_INDEX (Midi Through ← pressure remapper)" >> "$LOG_FILE"
  else
    SURGE_MIDI_ARGS=(--all-midi-inputs)
    echo "$(date): WARNING: Midi Through not found — falling back to --all-midi-inputs (Touch fader inactive)" >> "$LOG_FILE"
  fi
else
  SURGE_MIDI_ARGS=(--all-midi-inputs)
  echo "$(date): Surge MIDI all inputs (remapper disabled)" >> "$LOG_FILE"
fi

# ============================================================================
# Engine selection — JACK, or a hard failure. No alternate audio route exists.
# ============================================================================
ACTIVE_ENGINE=""
ENGINE_STATE=""
ENGINE_REASON=""
SURGE_AUDIO_ARGS=()

JACK_READY_TIMEOUT="$(mpe_jack_ready_timeout)"
if mpe_wait_for_jack_server "$JACK_READY_TIMEOUT"; then
    JACK_INDEX="$(resolve_jack_device_index)" || JACK_INDEX=""
    if [ -n "$JACK_INDEX" ]; then
        AUDIO_DEVICE="$JACK_INDEX"
        ACTIVE_ENGINE=jack
        ENGINE_STATE=ok
        SURGE_AUDIO_ARGS=(
            --audio-interface="$AUDIO_DEVICE"
            --sample-rate="$SURGE_SAMPLE_RATE"
        )
        engine_log "JACK client: audio interface $AUDIO_DEVICE, ${SURGE_SAMPLE_RATE} Hz"
        # Spec D4: jackd assigns the client audio thread its priority (measured
        # jackd 70 / Surge 65). No chrt wrapper here — it would fight the server
        # and elevate non-audio threads too. MPE_SURGE_RT_PRIORITY had no other
        # consumer once ALSA mode's chrt path was removed alongside it.
    else
        ENGINE_REASON="no-jack-device"
        engine_log "CRITICAL: jackd is up but Surge lists no JACK output device (reason=$ENGINE_REASON)"
    fi
else
    ENGINE_REASON="no-server"
    engine_log "CRITICAL: no JACK server after ${JACK_READY_TIMEOUT}s (reason=$ENGINE_REASON)"
fi

if [ -z "$ACTIVE_ENGINE" ]; then
    ENGINE_STATE=failed
    engine_log "CRITICAL: engine=jack state=failed reason=$ENGINE_REASON — no graph server available. No ALSA fallback exists; the appliance stays silent until jackd recovers."
    engine_log "CRITICAL: see $LOG_FILE and 'journalctl -u mpe-jackd' for the cause; check the DAC connection"
    mpe_publish_jack_engine_failure "$ENGINE_REASON"
    exit 1
fi

"$SURGE_CLI" \
  "${SURGE_MIDI_ARGS[@]}" \
  --mpe-enable \
  --mpe-pitch-bend-range=48 \
  "${SURGE_AUDIO_ARGS[@]}" \
  --osc-in-port=53280 \
  --osc-out-port=53270 \
  --no-stdin \
  >> "$LOG_FILE" 2>&1 &

SURGE_PID=$!
echo "$(date): Surge XT CLI started with PID $SURGE_PID (Audio device: $AUDIO_DEVICE)" >> "$LOG_FILE"
engine_log "Surge XT CLI running (PID: $SURGE_PID) engine=$MPE_ENGINE_NAME active=$ACTIVE_ENGINE state=$ENGINE_STATE device=$AUDIO_DEVICE"

# Published for `mpe engine status`, the supervisor, and the touch HUD.
mpe_surge_state_write "$ACTIVE_ENGINE" "$AUDIO_DEVICE"
mpe_engine_state_write "$MPE_ENGINE_NAME" "$ACTIVE_ENGINE" "$ENGINE_STATE" "$ENGINE_REASON" "$(mpe_looper_state_label)"

sleep 2
