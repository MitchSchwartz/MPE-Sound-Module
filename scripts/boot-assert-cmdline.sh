#!/bin/bash
# Assert ship-critical kernel cmdline flags — fail loudly if absent.
#
# Pattern matches jackd-prestart.sh: a setting that exists only on the SD card
# is not configuration, it is a liability. Called from mpe-jackd ExecStartPre.

set -euo pipefail

CMDLINE="$(tr '\0' ' ' < /proc/cmdline 2>/dev/null || true)"
missing=0

_require_token() {
    local token="$1"
    case " $CMDLINE " in
        *" $token "*) return 0 ;;
        *) echo "boot-assert-cmdline: MISSING required cmdline token: $token" >&2; missing=1 ;;
    esac
}

_require_token "irqaffinity=0,1"

# Disable disconnected HDMI connectors; keep DSI (card1-DSI-1). Requires reboot
# once applied via apply-appliance-hygiene.sh — assert only warns until present.
for port in "video=HDMI-A-1:d" "video=HDMI-A-2:d"; do
    case " $CMDLINE " in
        *" $port "*) ;;
        *) echo "boot-assert-cmdline: WARN optional (HDMI off): $port not in cmdline yet" >&2 ;;
    esac
done

if [ "$missing" -ne 0 ]; then
    echo "boot-assert-cmdline: fix /boot/firmware/cmdline.txt and reboot" >&2
    exit 1
fi

echo "boot-assert-cmdline: ok (irqaffinity=0,1)"
exit 0
