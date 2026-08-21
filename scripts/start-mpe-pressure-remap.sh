#!/usr/bin/env bash
# Start pressure remap only when Roli USB + ALSA MIDI port are both present.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

if ! lsusb 2>/dev/null | grep -qi '2af4:'; then
    echo "mpe-pressure-remap: no Roli USB — idle exit 0"
    exit 0
fi

bash "$SCRIPT_DIR/wait-for-usb-midi.sh"

if ! aconnect -l 2>/dev/null | grep -qiE 'lumi|seaboard|roli'; then
    echo "mpe-pressure-remap: Roli USB without ALSA MIDI port — idle exit 0"
    exit 0
fi

exec python3 "$MPE_MODULE_REPO/scripts/mpe-pressure-remap.py"
