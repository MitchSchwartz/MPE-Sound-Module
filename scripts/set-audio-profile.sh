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
# shellcheck source=lib/audio-settings-pending.sh
source "$SCRIPT_DIR/lib/audio-settings-pending.sh"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found — run configure-pi-paths.sh first" >&2
    exit 1
fi

# This script is the OTHER door into the failure that took the appliance down on
# 2026-09-01: it mutates /etc/mpe/mpe.env and then restarts the audio graph
# through the same mpe_promote_surge_planned path as set-surge-audio.sh. It had
# neither the lock nor the crash marker, and no rollback at ALL — on a failed
# graph change it exited 1 with the new profile still written. A kill or a bad
# profile therefore persisted across the reboot exactly as a bad buffer did.
_LOCK_DIR="/run/mpe"
mkdir -p "$_LOCK_DIR" 2>/dev/null || _LOCK_DIR="${TMPDIR:-/tmp}"
# See set-surge-audio.sh: `exec 9>FILE 2>/dev/null` permanently redirects this
# script's stderr, silencing every later diagnostic. Open the file separately so
# a failure is reportable.
if : > "$_LOCK_DIR/set-surge-audio.lock" 2>/dev/null; then
    exec 9>"$_LOCK_DIR/set-surge-audio.lock"
else
    echo "WARNING: cannot create $_LOCK_DIR/set-surge-audio.lock — proceeding" >&2
    echo "         without serialisation." >&2
fi
if [ -e /dev/fd/9 ] && command -v flock >/dev/null 2>&1; then
    if ! flock -n 9; then
        echo "ERROR: another audio settings change is already running — refusing to" >&2
        echo "       start a second one." >&2
        exit 1
    fi
fi

# Shares set-surge-audio.sh's lock file on purpose: a profile change and a buffer
# change both rewrite mpe.env and both restart the graph, so they must exclude
# each other, not just themselves.
_prev_profile="$(sed -n 's/^MPE_AUDIO_PROFILE=//p' "$ENV_FILE" | tail -1)"
_prev_profile="${_prev_profile:-standalone}"
mpe_pending_write "$ENV_FILE" "MPE_AUDIO_PROFILE=$_prev_profile" \
    || echo "WARNING: could not write the pending-settings marker — a kill mid-change will not self-heal" >&2

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
if ! mpe_promote_surge_planned "profile-change"; then
    echo "ERROR: profile graph change failed — restoring $_prev_profile" >&2
    tmp="$(mktemp)"
    sed "s/^MPE_AUDIO_PROFILE=.*/MPE_AUDIO_PROFILE=$_prev_profile/" "$ENV_FILE" >"$tmp"
    install -m 0644 "$tmp" "$ENV_FILE"
    rm -f "$tmp"
    export MPE_AUDIO_PROFILE="$_prev_profile"
    mpe_pending_clear
    if mpe_promote_surge_planned "rollback-after-failed-profile"; then
        echo "ERROR: profile '$PROFILE' not usable — reverted to $_prev_profile" >&2
    else
        echo "ERROR: graph failed and rollback failed — check journalctl -u mpe-jackd -u surge-xt-cli" >&2
    fi
    exit 1
fi

# Proven: the graph came up on the new profile.
mpe_pending_clear
# profile_switch_flag_mark: consumed by start-surge-cli.sh on the next Surge start
# (skips USB MIDI wait).

echo "MPE_AUDIO_PROFILE=$PROFILE saved — audio graph restored"
