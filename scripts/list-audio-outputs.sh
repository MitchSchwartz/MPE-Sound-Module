#!/bin/bash
# List the audio outputs that can be SELECTED, one record per line:
#
#   index|card_id|key|speed|product
#
# The key is what a stored selection holds (usb:VID:PID[:SERIAL]); an empty key
# means the device has no USB identity and so can be bound automatically but not
# chosen. Selectable excludes virtual cards and anything with no playback PCM --
# the LUMI Keys BLOCK and the APC mini both enumerate as USB-Audio and both kill
# jackd if bound.
#
# This exists so the touch UI and mpe-cli read the SAME enumeration the graph
# start uses, rather than each growing their own. That divergence is the bug
# Documents/specs/audio-output-selection-spec.md exists to prevent, and it had
# already happened four ways with the buffer list.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"
# shellcheck source=lib/audio-outputs.sh
source "$SCRIPT_DIR/lib/audio-outputs.sh"

mpe_output_records
