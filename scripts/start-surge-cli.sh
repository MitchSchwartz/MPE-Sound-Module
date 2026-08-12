#!/bin/bash
# Surge XT CLI - Headless startup script with robust audio device detection
#
# Engine selection (spec Documents/specs/jack-audio-engine-spec.md):
#   MPE_AUDIO_ENGINE=jack (default) — Surge joins the jackd graph; jackd owns the
#       device and assigns the client audio thread its priority.
#   MPE_AUDIO_ENGINE=alsa           — unchanged legacy path: tier device, ALSA
#       buffer size, optional chrt.
#
# The instrument must never boot silent. If the graph server is not accepting
# clients within a bounded wait, this script logs ENGINE-FALLBACK and starts
# Surge on the ALSA tier device (spec D3 `degraded`) instead of exiting.

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

echo "$(date): Starting Surge XT CLI..." >> "$LOG_FILE"

# Engine decisions must be visible in `journalctl -u surge-xt-cli`, not only in
# the private log file — the fallback is the thing you go looking for at a gig.
engine_log() {
    echo "$(date): $1" >> "$LOG_FILE"
    echo "$1"
}

AUDIO_ENGINE="$(mpe_audio_engine)"
engine_log "Audio engine: $AUDIO_ENGINE (MPE_AUDIO_ENGINE=${MPE_AUDIO_ENGINE:-unset, default $MPE_AUDIO_ENGINE_DEFAULT})"

AUDIO_DEVICE=""
DEVICE_NAME=""
DEVICE_TIER=""
ALSA_FAIL_REASON=""

select_alsa_device() {
    local result exit_code
    result=$("$SCRIPT_DIR/detect-audio-device.sh" "$SURGE_CLI" 2>&1)
    exit_code=$?

    if [ $exit_code -ne 0 ]; then
        ALSA_FAIL_REASON="no-alsa-device"
        echo "$(date): CRITICAL - Audio detection failed completely" >> "$LOG_FILE"
        echo "$result" >> "$LOG_FILE"
        return 1
    fi

    AUDIO_DEVICE=$(echo "$result" | grep "^DEVICE_ID=" | cut -d= -f2)
    DEVICE_NAME=$(echo "$result" | grep "^DEVICE_NAME=" | cut -d= -f2)
    DEVICE_TIER=$(echo "$result" | grep "^TIER=" | cut -d= -f2)

    if [ -z "$AUDIO_DEVICE" ]; then
        ALSA_FAIL_REASON="no-alsa-device"
        echo "$(date): CRITICAL - Audio detection returned no device id" >> "$LOG_FILE"
        echo "$result" >> "$LOG_FILE"
        return 1
    fi

    echo "$(date): Selected audio device: $AUDIO_DEVICE" >> "$LOG_FILE"
    echo "$(date):   Name: $DEVICE_NAME" >> "$LOG_FILE"
    echo "$(date):   Tier: $DEVICE_TIER" >> "$LOG_FILE"
    return 0
}

# JUCE exposes the graph as an audio device of type JACK; the index only exists
# while a server is up. Anchor on the device-listing prefix — libjack's own
# diagnostics also mention "Jack".
resolve_jack_device_index() {
    "$SURGE_CLI" --list-devices 2>&1 \
        | grep "Output Audio Device" \
        | grep -i "JACK" \
        | sed -n 's/.*\[\([0-9][0-9]*\.[0-9][0-9]*\)\].*/\1/p' \
        | head -1
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

# Drop idle ALSA loopback from calibration — unless looper routing is enabled.
if [ "${MPE_LOOPER_ENABLED:-0}" != "1" ]; then
    # shellcheck source=lib/unload-snd-aloop.sh
    source "$SCRIPT_DIR/lib/unload-snd-aloop.sh"
else
    sudo modprobe snd-aloop 2>/dev/null || true
    echo "$(date): MPE_LOOPER_ENABLED=1 — keeping snd-aloop loaded" >> "$LOG_FILE"
fi

SURGE_BUFFER_SIZE="${MPE_SURGE_BUFFER_SIZE:-1024}"
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
# Engine selection — JACK first (default), ALSA tier as the guaranteed fallback
# ============================================================================
ACTIVE_ENGINE=""
ENGINE_STATE=""
ENGINE_REASON=""
SURGE_LAUNCH_PREFIX=()
SURGE_AUDIO_ARGS=()

if [ "$AUDIO_ENGINE" = jack ]; then
    JACK_READY_TIMEOUT="$(mpe_jack_ready_timeout)"
    if mpe_wait_for_jack_server "$JACK_READY_TIMEOUT"; then
        JACK_INDEX="$(resolve_jack_device_index)"
        if [ -n "$JACK_INDEX" ]; then
            AUDIO_DEVICE="$JACK_INDEX"
            DEVICE_NAME="JACK graph server"
            DEVICE_TIER="jack"
            ACTIVE_ENGINE=jack
            ENGINE_STATE=ok
            ENGINE_REASON=""
            SURGE_AUDIO_ARGS=(
                --audio-interface="$AUDIO_DEVICE"
                --sample-rate="$SURGE_SAMPLE_RATE"
            )
            engine_log "JACK client: audio interface $AUDIO_DEVICE, ${SURGE_SAMPLE_RATE} Hz"
            engine_log "JACK mode: period is a server property — MPE_SURGE_BUFFER_SIZE=$SURGE_BUFFER_SIZE ignored (see MPE_JACK_BUFFER)"
            # Spec D4: jackd assigns the client audio thread its priority
            # (measured jackd 70 / Surge 65). A chrt wrapper would fight the
            # server and elevate non-audio threads too.
            engine_log "JACK mode: MPE_SURGE_RT_PRIORITY=${MPE_SURGE_RT_PRIORITY:-unset} IGNORED — jackd assigns the client audio thread priority"
        else
            ENGINE_REASON="no-jack-device"
            engine_log "ENGINE-FALLBACK: jackd is up but Surge lists no JACK output device (reason=$ENGINE_REASON) — falling back to ALSA"
        fi
    else
        ENGINE_REASON="no-server"
        engine_log "ENGINE-FALLBACK: no JACK server after ${JACK_READY_TIMEOUT}s (reason=$ENGINE_REASON) — falling back to ALSA"
    fi
fi

if [ -z "$ACTIVE_ENGINE" ]; then
    if select_alsa_device; then
        ACTIVE_ENGINE=alsa
        if [ "$AUDIO_ENGINE" = jack ]; then
            ENGINE_STATE=degraded
            engine_log "ENGINE-FALLBACK: running on ALSA tier $DEVICE_TIER device $AUDIO_DEVICE ($DEVICE_NAME) — sound with worse latency"
        else
            ENGINE_STATE=ok
        fi
        SURGE_AUDIO_ARGS=(
            --audio-interface="$AUDIO_DEVICE"
            --buffer-size="$SURGE_BUFFER_SIZE"
            --sample-rate="$SURGE_SAMPLE_RATE"
        )
        echo "$(date): ALSA buffer size: $SURGE_BUFFER_SIZE samples" >> "$LOG_FILE"
        echo "$(date): Sample rate: $SURGE_SAMPLE_RATE Hz" >> "$LOG_FILE"

        # Optional SCHED_FIFO for the whole Surge process (ALSA mode only). Off by
        # default: LimitRTPRIO in surge-xt-cli.service already lets JUCE elevate
        # just its audio thread, which is safer. Use this only if `chrt -p <pid>`
        # still shows SCHED_OTHER under load. See docs/LATENCY-SPIKE.md (Arm A½).
        case "${MPE_SURGE_RT_PRIORITY:-0}" in
          '' | 0) ;;
          *[!0-9]*)
            echo "$(date): WARNING: MPE_SURGE_RT_PRIORITY not a number — ignoring" >> "$LOG_FILE"
            ;;
          *)
            if command -v chrt > /dev/null 2>&1; then
              SURGE_LAUNCH_PREFIX=(chrt --fifo "$MPE_SURGE_RT_PRIORITY")
              echo "$(date): SCHED_FIFO priority $MPE_SURGE_RT_PRIORITY" >> "$LOG_FILE"
            else
              echo "$(date): WARNING: chrt not found — staying SCHED_OTHER" >> "$LOG_FILE"
            fi
            ;;
        esac
    else
        # Spec D3 `failed`: no server AND no usable ALSA device. Name both causes
        # rather than exiting 1 silently as this script used to.
        ENGINE_STATE=failed
        if [ "$AUDIO_ENGINE" = jack ]; then
            ENGINE_REASON="${ENGINE_REASON:-no-server}+${ALSA_FAIL_REASON:-no-alsa-device}"
            engine_log "CRITICAL: engine=jack state=failed reason=$ENGINE_REASON — no graph server AND no usable ALSA device"
        else
            ENGINE_REASON="${ALSA_FAIL_REASON:-no-alsa-device}"
            engine_log "CRITICAL: engine=alsa state=failed reason=$ENGINE_REASON — no usable ALSA device"
        fi
        engine_log "CRITICAL: see $LOG_FILE for the device list; check the DAC connection"
        mpe_engine_state_write "$AUDIO_ENGINE" none failed "$ENGINE_REASON" "$(mpe_looper_state_label)"
        mpe_surge_state_write none ""
        exit 1
    fi
fi

"${SURGE_LAUNCH_PREFIX[@]}" "$SURGE_CLI" \
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
engine_log "Surge XT CLI running (PID: $SURGE_PID) engine=$AUDIO_ENGINE active=$ACTIVE_ENGINE state=$ENGINE_STATE device=$AUDIO_DEVICE"

# Published for `mpe engine status`, the supervisor, and the touch HUD.
mpe_surge_state_write "$ACTIVE_ENGINE" "$AUDIO_DEVICE"
mpe_engine_state_write "$AUDIO_ENGINE" "$ACTIVE_ENGINE" "$ENGINE_STATE" "$ENGINE_REASON" "$(mpe_looper_state_label)"

sleep 2
