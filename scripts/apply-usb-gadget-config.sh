#!/usr/bin/env bash
# Idempotently set dwc2 USB-C peripheral mode for UAC2 gadget (usb-host profile).
#
# Pi 5: RP1 USB-A host ports (Sound Blaster, LUMI) stay host — only SoC USB-C flips.
# Pi 4: peripheral belongs under [pi4] only (not [all]) — see docs/USB-AUDIO-HOST.md.
#
# Usage (on Pi, root):
#   sudo ./scripts/apply-usb-gadget-config.sh [--dry-run] [--verify]
#
# Reboot with USB-C data unplugged from the laptop, then plug in after SSH is up.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/detect-pi-platform.sh
source "$SCRIPT_DIR/lib/detect-pi-platform.sh"

DRY=false
VERIFY=false
PERIPHERAL='dtoverlay=dwc2,dr_mode=peripheral'

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=true; shift ;;
        --verify) VERIFY=true; shift ;;
        -h|--help)
            echo "Usage: sudo $0 [--dry-run] [--verify]" >&2
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ "$VERIFY" = true ] && [ "$DRY" = true ]; then
    echo "ERROR: --verify and --dry-run are mutually exclusive." >&2
    exit 2
fi

if ! mpe_is_raspberry_pi; then
    echo "apply-usb-gadget-config: not a Raspberry Pi — skipping" >&2
    exit 0
fi

CONFIG="/boot/firmware/config.txt"
if [ ! -f "$CONFIG" ]; then
    CONFIG="/boot/config.txt"
fi
if [ ! -f "$CONFIG" ]; then
    echo "ERROR: config.txt not found under /boot/firmware or /boot" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ] && [ "$VERIFY" = false ]; then
    echo "ERROR: run as root (sudo $0)" >&2
    exit 1
fi

_verify_fail() {
    echo "VERIFY FAIL: $*" >&2
    exit 1
}

_has_active_peripheral() {
    grep -qE '^[[:space:]]*dtoverlay=dwc2,dr_mode=peripheral' "$CONFIG"
}

_has_active_host() {
    grep -qE '^[[:space:]]*dtoverlay=dwc2,dr_mode=host' "$CONFIG"
}

if [ "$VERIFY" = true ]; then
    _has_active_peripheral || _verify_fail "missing active $PERIPHERAL in $CONFIG"
    if _has_active_host; then
        _verify_fail "dtoverlay=dwc2,dr_mode=host still active (conflicts with gadget)"
    fi
    echo "apply-usb-gadget-config --verify: ok ($CONFIG)"
    exit 0
fi

plat="$(mpe_detect_pi_platform)"
echo "apply-usb-gadget-config: platform=$plat model=$(mpe_pi_model_string)"

if _has_active_peripheral && ! _has_active_host; then
    echo "config.txt already has dwc2 peripheral — no change"
    exit 0
fi

tmp="$(mktemp)"
changed=0
host_replaced=0

while IFS= read -r line || [ -n "$line" ]; do
    stripped="${line#"${line%%[![:space:]]*}"}"
    case "$stripped" in
        dtoverlay=dwc2,dr_mode=host)
            echo "$PERIPHERAL" >>"$tmp"
            changed=1
            host_replaced=1
            continue
            ;;
        dtoverlay=dwc2,dr_mode=peripheral)
            echo "$stripped" >>"$tmp"
            continue
            ;;
    esac
    echo "$line" >>"$tmp"
done < "$CONFIG"

if [ "$host_replaced" -eq 0 ] && ! _has_active_peripheral; then
    case "$plat" in
        pi4)
            {
                echo ""
                echo "# MPE USB host audio — apply-usb-gadget-config.sh"
                echo "[pi4]"
                echo "$PERIPHERAL"
            } >>"$tmp"
            ;;
        pi5)
            {
                echo ""
                echo "# MPE USB host audio — apply-usb-gadget-config.sh"
                echo "[pi5]"
                echo "$PERIPHERAL"
            } >>"$tmp"
            ;;
        *)
            {
                echo ""
                echo "# MPE USB host audio — apply-usb-gadget-config.sh"
                echo "$PERIPHERAL"
            } >>"$tmp"
            ;;
    esac
    changed=1
fi

if [ "$changed" -eq 0 ]; then
    rm -f "$tmp"
    echo "config.txt unchanged"
    exit 0
fi

if [ "$DRY" = true ]; then
    cat "$tmp"
    rm -f "$tmp"
    exit 0
fi

cp "$CONFIG" "${CONFIG}.bak.usb-gadget.$(date +%Y%m%d%H%M%S)"
mv "$tmp" "$CONFIG"
echo "Updated $CONFIG (backup saved alongside)"
echo "Reboot required. Unplug USB-C data from the laptop before reboot."
