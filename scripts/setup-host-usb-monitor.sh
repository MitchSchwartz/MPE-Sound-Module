#!/bin/bash
# Optional host-side WirePlumber drop-in for usb-host profile.
#
# Run on the LAPTOP/desk PC (not the Pi). Installs one drop-in that prevents
# WirePlumber from suspending the MPE gadget capture node between DAW sessions.
#
# This is OPTIONAL — plug-and-play works without it:
#   1. Pi stall watchdog (automatic) recovers Surge when your DAW opens the input
#   2. Select "USB Audio Passthrough" / "MPE Sound Module" as a normal capture
#      input in REAPER, Ardour, etc. — no loopback or per-DAW routing required
#
# Install this only if you notice a delay or missed notes when first arming a
# track after plugging in (usually not needed on Linux).
#
# Usage: setup-host-usb-monitor.sh [--uninstall] [--no-restart]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SRC_DIR="$REPO_ROOT/config/host"

WP_DIR="$HOME/.config/wireplumber/wireplumber.conf.d"
WP_CONF="51-mpe-usb-no-suspend.conf"

UNINSTALL=false
RESTART=true
for arg in "$@"; do
    case "$arg" in
        --uninstall) UNINSTALL=true ;;
        --no-restart) RESTART=false ;;
        *)
            echo "Usage: $0 [--uninstall] [--no-restart]" >&2
            exit 1
            ;;
    esac
done

restart_stack() {
    [ "$RESTART" = true ] || return 0
    if ! command -v systemctl >/dev/null 2>&1; then
        echo "  (systemctl not found — restart PipeWire manually)"
        return 0
    fi
    systemctl --user restart pipewire.service pipewire-pulse.service wireplumber.service 2>/dev/null ||
        echo "  WARN: could not restart the PipeWire user stack — log out/in to apply"
    sleep 2
}

if [ "$UNINSTALL" = true ]; then
    rm -f "$WP_DIR/$WP_CONF"
    echo "Removed host WirePlumber drop-in."
    restart_stack
    exit 0
fi

if ! command -v pw-cli >/dev/null 2>&1; then
    echo "ERROR: PipeWire not found on this machine. Run this on the host PC, not the Pi." >&2
    exit 1
fi

mkdir -p "$WP_DIR"
install -m 0644 "$SRC_DIR/$WP_CONF" "$WP_DIR/$WP_CONF"
echo "Installed (optional): $WP_DIR/$WP_CONF"

restart_stack

if pactl list sources short 2>/dev/null | grep -qi 'MPE_Sound_Module'; then
    echo "  ✓ gadget source present — select it as a capture input in your DAW"
else
    echo "  ! gadget source not found — plug in the Pi (usb-host profile) and re-check"
fi
