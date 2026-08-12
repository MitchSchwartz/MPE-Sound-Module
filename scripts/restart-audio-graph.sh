#!/bin/bash
# Restart whatever owns the audio device for the current engine (spec D2).
#
#   engine=jack  → mpe-jackd.service   (jackd binds one device at start; Surge is
#                                       reconciled onto the new server by
#                                       surge-watchdog.sh)
#   engine=alsa  → surge-xt-cli.service (Surge owns the device directly)
#
# Entry point for callers that cannot source shell libraries — notably
# config/99-usb-audio.rules, where a DAC unplug/replug must restart the graph
# rather than only Surge (criterion 15). udev kills long-running RUN commands,
# so the restart is issued with --no-block.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"

UNIT="$(mpe_audio_graph_unit)"

if mpe_restart_audio_graph; then
    echo "restart-audio-graph: restarted $UNIT (engine=$(mpe_audio_engine))"
else
    echo "restart-audio-graph: FAILED to restart $UNIT (engine=$(mpe_audio_engine))" >&2
    exit 1
fi
