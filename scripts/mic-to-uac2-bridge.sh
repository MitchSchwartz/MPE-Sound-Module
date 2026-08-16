#!/bin/bash
# Route Sound Blaster mic in (RC-5 return) → UAC2 gadget → host PC capture.
# Used with MPE_AUDIO_PROFILE=usb-host-session — Surge stays on Sound Blaster out.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

LOG="${MPE_MIC_BRIDGE_LOG:-$HOME/mic-to-uac2-bridge.log}"
RATE="${MPE_SURGE_SAMPLE_RATE:-48000}"
BUF="${MPE_MIC_BRIDGE_BUFFER_SIZE:-512}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S'): $*" | tee -a "$LOG" >&2
}

resolve_devices() {
    python3 - "$REPO_ROOT" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from patch_browser.session_capture import (
    resolve_blaster_mic_capture_device,
    resolve_uac2_playback_device,
)
cap = resolve_blaster_mic_capture_device()
play = resolve_uac2_playback_device()
if not cap or not play:
    raise SystemExit(1)
print(cap)
print(play)
PY
}

if [ "${MPE_AUDIO_PROFILE:-}" != "usb-host-session" ]; then
    log "Profile is ${MPE_AUDIO_PROFILE:-standalone} — bridge not started"
    exit 0
fi

mapfile -t devs < <(resolve_devices) || {
    log "ERROR: could not resolve mic capture and UAC2 playback devices"
    exit 1
}
CAP_DEV="${devs[0]}"
PLAY_DEV="${devs[1]}"

log "Starting mic → UAC2 bridge: capture=$CAP_DEV playback=$PLAY_DEV rate=$RATE buffer=$BUF"

# Sound Blaster hw capture is stereo-only; plughw accepts mono but stereo passthrough
# is simpler and matches RC-5 return on both channels.
# Foreground pipeline — systemd Type=simple keeps this service alive until capture ends.
arecord -D "$CAP_DEV" -f S16_LE -r "$RATE" -c 2 -t raw --buffer-size="$BUF" 2>>"$LOG" |
    aplay -D "$PLAY_DEV" -f S16_LE -r "$RATE" -c 2 -t raw --buffer-size="$BUF" 2>>"$LOG"
status=$?
log "mic → UAC2 bridge stopped (exit $status)"
exit "$status"
