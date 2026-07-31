#!/bin/bash
# Idempotently add DSI-friendly kernel cmdline flags on Raspberry Pi OS.
#
# Redirects boot console to serial and keeps fbcon off the panel so kernel
# scroll does not flash on the SmartiPi DSI before touch-boot-animation starts.
#
# Usage (on Pi):
#   sudo ./scripts/apply-dsi-cmdline.sh

set -euo pipefail

STRIP_TTY1=false
for arg in "$@"; do
    case "$arg" in
        --strip-tty1) STRIP_TTY1=true ;;
        -h|--help)
            echo "Usage: sudo $0 [--strip-tty1]" >&2
            echo "  --strip-tty1  Remove console=tty1 (aggressive DSI; keep serial attached)" >&2
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 1
            ;;
    esac
done

CMDLINE="/boot/firmware/cmdline.txt"
if [ ! -f "$CMDLINE" ]; then
    CMDLINE="/boot/cmdline.txt"
fi
if [ ! -f "$CMDLINE" ]; then
    echo "ERROR: cmdline.txt not found under /boot/firmware or /boot" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo $0)" >&2
    exit 1
fi

line="$(tr -d '\n' < "$CMDLINE")"
changed=0

remove_token() {
    local token="$1"
    if [[ " $line " != *" $token "* ]]; then
        return
    fi
    line="${line// $token/}"
    line="${line//$token /}"
    line="${line//$token/}"
    changed=1
}

add_token() {
    local token="$1"
    if [[ " $line " == *" $token "* ]]; then
        return
    fi
    line="$line $token"
    changed=1
}

# Prefer serial console; keep framebuffer console off the DSI.
# Do NOT remove console=tty1 by default — some Pi/firmware combos hang early boot
# without it. Use --strip-tty1 only after you have serial console to recover.
add_token "console=serial0,115200"
add_token "fbcon=map:0"
# Reduce kernel scroll noise on the panel without dropping tty1.
add_token "loglevel=3"
add_token "logo.nologo"

if [ "$STRIP_TTY1" = true ]; then
    remove_token "console=tty1"
fi

if [ "$changed" -eq 0 ]; then
    echo "cmdline already has DSI console flags — no change"
    exit 0
fi

cp "$CMDLINE" "${CMDLINE}.bak.$(date +%Y%m%d%H%M%S)"
printf '%s\n' "$line" > "$CMDLINE"
echo "Updated $CMDLINE (backup saved alongside)"
echo "Reboot the Pi for cmdline changes to take effect."
