#!/usr/bin/env bash
# Start the MIDI router.
#
# These gates predate classic-MIDI support, when a ROLI was the only thing the
# daemon could bind and starting without one was pointless. With
# MPE_ROUTE_CLASSIC=1 that is no longer true: a plain keyboard is a valid and
# sufficient reason to run, and exiting here meant an appliance with only a
# classic keyboard never started the router at all -- before any Python ran,
# so nothing in the daemon's own logic could compensate.
#
# Measured on the appliance 2026-08-28: with the ROLI off the bus, the service
# reported "no Roli USB - idle exit 0" and went inactive while an APC mini mk2
# sat plugged in and ready.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

ROUTE_CLASSIC="${MPE_ROUTE_CLASSIC:-0}"

if [ "$ROUTE_CLASSIC" != "0" ] && [ -n "$ROUTE_CLASSIC" ]; then
    # Any controller will do. The daemon waits for one rather than exiting,
    # so there is nothing useful to decide here.
    bash "$SCRIPT_DIR/wait-for-usb-midi.sh" || true
else
    if ! lsusb 2>/dev/null | grep -qi '2af4:'; then
        echo "mpe-pressure-remap: no Roli USB and classic routing off — idle exit 0"
        exit 0
    fi

    bash "$SCRIPT_DIR/wait-for-usb-midi.sh"

    if ! aconnect -l 2>/dev/null | grep -qiE 'lumi|seaboard|roli'; then
        echo "mpe-pressure-remap: Roli USB without ALSA MIDI port — idle exit 0"
        exit 0
    fi
fi

exec python3 "$MPE_MODULE_REPO/scripts/mpe-pressure-remap.py"
