#!/bin/bash
# Apply persisted MPE_AUDIO_PROFILE from /etc/mpe/mpe.env at boot — enable/disable
# gadget + stall watchdog to match, without restarting Surge (surge-xt-cli starts next).
# Host-route watcher starts after Surge via surge-xt-cli ExecStartPost.

set -euo pipefail

export MPE_BOOT_PROFILE_SYNC=1

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

mpe_source_appliance_env

mpe_enable_usb_audio_gadget

# shellcheck source=lib/dac-volume.sh
source "$SCRIPT_DIR/lib/dac-volume.sh"
mpe_apply_dac_volume || true
