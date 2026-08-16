#!/bin/bash
# Start the UAC2 host-route watcher after Surge is up (usb-host / usb-host-session).
# Invoked from surge-xt-cli.service ExecStartPost (+ = root) so boot and profile
# switches never start the watcher before Surge (avoids restart races / deadlocks).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

mpe_source_appliance_env

if [ "${MPE_AUDIO_PROFILE:-standalone}" != "usb-host" ] \
    && [ "${MPE_AUDIO_PROFILE:-standalone}" != "usb-host-session" ]; then
    exit 0
fi

if systemctl is-active --quiet uac2-stall-watchdog.service 2>/dev/null; then
    exit 0
fi

systemctl start uac2-stall-watchdog.service
