#!/usr/bin/env bash
# Idempotently configure /boot/firmware/config.txt for Freenove 5″ DSI touch panel.
# Sets dtoverlay=vc4-kms-dsi-7inch and disables display_auto_detect (commented).
#
# Usage (on Pi, root):
#   sudo ./scripts/apply-dsi-config.sh [--dry-run] [--verify]
#
# See docs/PI5-PLAYER-SETUP-LOG.md §C3

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/detect-pi-platform.sh
source "$SCRIPT_DIR/lib/detect-pi-platform.sh"

DRY=false
VERIFY=false
OVERLAY="vc4-kms-dsi-7inch"

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
    echo "apply-dsi-config: not a Raspberry Pi — skipping" >&2
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

if [ "$VERIFY" = true ]; then
    grep -qE "^[[:space:]]*dtoverlay=${OVERLAY}" "$CONFIG" || \
        _verify_fail "missing active dtoverlay=${OVERLAY} in $CONFIG"
    if grep -qE '^[[:space:]]*display_auto_detect=1' "$CONFIG"; then
        _verify_fail "display_auto_detect=1 is still active (should be commented out)"
    fi
    echo "apply-dsi-config --verify: ok ($CONFIG)"
    exit 0
fi

plat="$(mpe_detect_pi_platform)"
echo "apply-dsi-config: platform=$plat model=$(mpe_pi_model_string)"

tmp="$(mktemp)"
changed=0
has_overlay=0

while IFS= read -r line || [ -n "$line" ]; do
    stripped="${line#"${line%%[![:space:]]*}"}"
    case "$stripped" in
        display_auto_detect=1)
            echo "#${stripped}  # disabled by apply-dsi-config.sh" >>"$tmp"
            changed=1
            continue
            ;;
        dtoverlay=${OVERLAY})
            has_overlay=1
            echo "$stripped" >>"$tmp"
            continue
            ;;
        dtoverlay=*)
            if [ "${stripped#\#}" = "$stripped" ]; then
                echo "# ${stripped}  # disabled by apply-dsi-config.sh" >>"$tmp"
                changed=1
                continue
            fi
            ;;
    esac
    echo "$line" >>"$tmp"
done < "$CONFIG"

if [ "$has_overlay" -eq 0 ]; then
    {
        echo ""
        echo "# Freenove 5″ DSI touch — apply-dsi-config.sh"
        echo "dtoverlay=${OVERLAY}"
    } >>"$tmp"
    changed=1
fi

if [ "$changed" -eq 0 ]; then
    rm -f "$tmp"
    echo "config.txt already has DSI overlay — no change"
    exit 0
fi

if [ "$DRY" = true ]; then
    cat "$tmp"
    rm -f "$tmp"
    exit 0
fi

cp "$CONFIG" "${CONFIG}.bak.$(date +%Y%m%d%H%M%S)"
mv "$tmp" "$CONFIG"
echo "Updated $CONFIG (backup saved alongside)"
echo "Reboot required for overlay changes."
