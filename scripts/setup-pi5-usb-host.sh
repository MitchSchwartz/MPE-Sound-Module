#!/usr/bin/env bash
# One-time enable USB direct (usb-host) on Pi 5 — UAC2 gadget + profile + services.
#
# Prerequisites: GPIO (or official PSU) power — NOT PD through the USB-C data port.
#
# Usage (on the Pi):
#   cd ~/MPE-Module && sudo ./scripts/setup-pi5-usb-host.sh
#   sudo ./scripts/setup-pi5-usb-host.sh --reboot    # apply + reboot (USB-C unplugged first)
#   sudo ./scripts/setup-pi5-usb-host.sh --verify    # post-reboot checks only
#
# After reboot: plug USB-A (laptop) → USB-C (Pi). In the DAW, arm capture @ 48 kHz —
# audio routes to the PC (~3–5 s, badge Sync). Headphones go quiet while capture is open.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/detect-pi-platform.sh
source "$SCRIPT_DIR/lib/detect-pi-platform.sh"

ENV_FILE="/etc/mpe/mpe.env"
DO_REBOOT=false
VERIFY_ONLY=false

while [ $# -gt 0 ]; do
    case "$1" in
        --reboot) DO_REBOOT=true; shift ;;
        --verify) VERIFY_ONLY=true; shift ;;
        -h|--help)
            sed -n '1,14p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo $0)" >&2
    exit 1
fi

if ! mpe_is_raspberry_pi; then
    echo "ERROR: Raspberry Pi only" >&2
    exit 1
fi

plat="$(mpe_detect_pi_platform)"
if [ "$plat" != pi5 ]; then
    echo "WARNING: platform=$plat (expected pi5) — continuing anyway" >&2
fi

if [ "$VERIFY_ONLY" = true ]; then
    "$SCRIPT_DIR/apply-usb-gadget-config.sh" --verify
    profile="$(grep -E '^MPE_AUDIO_PROFILE=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || echo standalone)"
    echo "MPE_AUDIO_PROFILE=${profile:-standalone}"
    if [ ! -f "$ENV_FILE" ]; then
        echo "VERIFY FAIL: $ENV_FILE missing" >&2
        exit 1
    fi
    case "${profile:-standalone}" in
        usb-host) ;;
        *) echo "VERIFY FAIL: profile is not usb-host" >&2; exit 1 ;;
    esac
    if [ ! -d /sys/class/udc ] || [ -z "$(ls -A /sys/class/udc 2>/dev/null || true)" ]; then
        echo "VERIFY FAIL: no UDC — peripheral overlay not active?" >&2
        exit 1
    fi
    "$SCRIPT_DIR/usb-host-verify.sh" || true
    exit 0
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE missing — run configure-pi-paths.sh first" >&2
    exit 1
fi

echo "== Step 1: boot config (dwc2 peripheral) =="
"$SCRIPT_DIR/apply-usb-gadget-config.sh"

echo ""
echo "== Step 2: appliance env =="
tmp="$(mktemp)"
if grep -q '^MPE_AUDIO_PROFILE=' "$ENV_FILE"; then
    sed 's/^MPE_AUDIO_PROFILE=.*/MPE_AUDIO_PROFILE=usb-host/' "$ENV_FILE" >"$tmp"
else
    cat "$ENV_FILE" >"$tmp"
    printf '\nMPE_AUDIO_PROFILE=usb-host\n' >>"$tmp"
fi
if ! grep -q '^MPE_USB_GADGET_PERSIST=' "$tmp"; then
    printf 'MPE_USB_GADGET_PERSIST=1\n' >>"$tmp"
fi
install -m 0644 "$tmp" "$ENV_FILE"
rm -f "$tmp"
echo "  MPE_AUDIO_PROFILE=usb-host"
echo "  MPE_USB_GADGET_PERSIST=1"

echo ""
echo "== Step 3: systemd units =="
export MPE_AUDIO_PROFILE=usb-host
"$SCRIPT_DIR/configure-pi-paths.sh" --local --force

echo ""
echo "== Done (reboot required) =="
echo ""
echo "Before reboot:"
echo "  1. Unplug USB-C DATA cable to the laptop (leave GPIO power on)."
echo "  2. Run: sudo reboot"
echo ""
echo "After reboot (~90 s):"
echo "  3. sudo ./scripts/setup-pi5-usb-host.sh --verify"
echo "  4. Plug USB-A (laptop) → USB-C (Pi data)."
echo "  5. On laptop: select 'USB Audio Passthrough' / 'MPE Sound Module' as INPUT @ 48000 Hz."
echo "  6. Arm a track — badge shows Sync briefly; audio goes to PC (DAC headphones mute)."
echo ""
echo "Toggle back: touch settings → Analog, or sudo ./scripts/set-audio-profile.sh standalone"
echo ""
echo "Full doc: docs/USB-AUDIO-HOST.md"

if [ "$DO_REBOOT" = true ]; then
    echo ""
    echo "Rebooting in 5 s (Ctrl+C to cancel)..."
    sleep 5
    systemctl reboot
fi
