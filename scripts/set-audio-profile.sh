#!/bin/bash
# Apply MPE_AUDIO_PROFILE on the Pi: update /etc/mpe/mpe.env, gadget service, Surge.
#
# Usage: sudo ./scripts/set-audio-profile.sh standalone|usb-host|usb-host-session
#
# Intended for NOPASSWD in sudoers (touch UI toggle). See docs/TOUCH_PATCH_BROWSER.md.

set -euo pipefail

PROFILE="${1:?usage: set-audio-profile.sh standalone|usb-host|usb-host-session}"
case "$PROFILE" in
    standalone | usb-host | usb-host-session) ;;
    *)
        echo "ERROR: profile must be standalone, usb-host, or usb-host-session" >&2
        exit 1
        ;;
esac

ENV_FILE="/etc/mpe/mpe.env"
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found — run configure-pi-paths.sh first" >&2
    exit 1
fi

tmp="$(mktemp)"
if grep -q '^MPE_AUDIO_PROFILE=' "$ENV_FILE"; then
    sed "s/^MPE_AUDIO_PROFILE=.*/MPE_AUDIO_PROFILE=$PROFILE/" "$ENV_FILE" >"$tmp"
else
    cat "$ENV_FILE" >"$tmp"
    printf '\nMPE_AUDIO_PROFILE=%s\n' "$PROFILE" >>"$tmp"
fi
install -m 0644 "$tmp" "$ENV_FILE"
rm -f "$tmp"

export MPE_AUDIO_PROFILE="$PROFILE"
mpe_enable_usb_audio_gadget
mpe_enable_audio_profile_sync

# shellcheck source=lib/wait-for-uac2-gadget.sh
source "$SCRIPT_DIR/lib/wait-for-uac2-gadget.sh"
if [ "$PROFILE" = "usb-host" ] || [ "$PROFILE" = "usb-host-session" ]; then
    wait_for_uac2_gadget 8 || true
fi

# shellcheck source=lib/profile-switch-flag.sh
source "$SCRIPT_DIR/lib/profile-switch-flag.sh"
# shellcheck source=lib/uac2-host-route.sh
source "$SCRIPT_DIR/lib/uac2-host-route.sh"
uac2_host_streaming_clear
profile_switch_flag_mark
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"
restart_audio_graph
# profile_switch_flag_mark: consumed by start-surge-cli.sh on the next Surge start
# (skips USB MIDI wait). jackd restart alone does not restart Surge, so the flag
# persists until Surge is next started — e.g. manual restart or supervisor reconcile.

echo "MPE_AUDIO_PROFILE=$PROFILE saved — restarting $(mpe_audio_graph_unit) (audio returns after graph settles)"
