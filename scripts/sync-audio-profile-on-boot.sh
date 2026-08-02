#!/bin/bash
# Apply persisted MPE_AUDIO_PROFILE from /etc/mpe/mpe.env at boot — enable/disable
# gadget + stall watchdog to match, without restarting Surge (surge-xt-cli starts next).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

mpe_source_appliance_env

mpe_enable_usb_audio_gadget
