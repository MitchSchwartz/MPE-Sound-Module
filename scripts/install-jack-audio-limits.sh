#!/usr/bin/env bash
# Ensure /etc/security/limits.d/audio.conf exists for shell-launched jackd / measurement harness.
# systemd units use LimitRTPRIO in unit files; this file matters for interactive ulimit -r.
#
# Usage (on Pi, root):
#   sudo ./scripts/install-jack-audio-limits.sh [--dry-run] [--verify]
#
# Also run from install-pi4-day0-tier1.sh / install-pi5-day0-tier1.sh after jackd2 debconf.

set -euo pipefail

TARGET="/etc/security/limits.d/audio.conf"
DRY=false
VERIFY=false

CANON='@audio   -  rtprio     95
@audio   -  memlock    unlimited
'

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

if [ "$VERIFY" = true ]; then
    if [ ! -f "$TARGET" ]; then
        echo "VERIFY FAIL: $TARGET missing" >&2
        exit 1
    fi
    grep -q 'rtprio' "$TARGET" && grep -q 'memlock' "$TARGET" || {
        echo "VERIFY FAIL: $TARGET missing rtprio or memlock lines" >&2
        exit 1
    }
    echo "install-jack-audio-limits --verify: ok"
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root (sudo $0)" >&2
    exit 1
fi

if [ -f "$TARGET" ] && grep -q 'rtprio' "$TARGET" && grep -q 'memlock' "$TARGET"; then
    echo "OK: $TARGET already present"
    exit 0
fi

if [ "$DRY" = true ]; then
    echo "would write $TARGET:"
    printf '%s\n' "$CANON"
    exit 0
fi

mkdir -p /etc/security/limits.d
if [ -f "$TARGET" ]; then
    cp "$TARGET" "${TARGET}.bak.$(date +%Y%m%d%H%M%S)"
fi
printf '%s\n' "$CANON" > "$TARGET"
echo "Wrote $TARGET"
