#!/bin/bash
# Restart the JACK graph server (spec D2) — mpe-jackd.service. jackd binds one
# device at start; Surge is reconciled onto the new server by
# surge-watchdog.sh, not restarted directly here.
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
CARD_ID="${1:-${SOUND_CARD_ID:-}}"

if [ -n "$CARD_ID" ] && mpe_should_skip_graph_restart_for_card "$CARD_ID"; then
    echo "restart-audio-graph: skipped (card=$CARD_ID)"
    exit 0
fi

if mpe_restart_audio_graph; then
    echo "restart-audio-graph: restarted $UNIT"
else
    echo "restart-audio-graph: FAILED to restart $UNIT" >&2
    exit 1
fi
