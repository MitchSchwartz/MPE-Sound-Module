#!/bin/bash
# Backup / apply / revert arm_freq=2000 in /boot/firmware/config.txt (P7).
#
# Usage:
#   sudo ./scripts/pi-overclock-config.sh backup
#   sudo ./scripts/pi-overclock-config.sh apply-2000    # arm_freq only, no over_voltage
#   sudo ./scripts/pi-overclock-config.sh revert
#   sudo ./scripts/pi-overclock-config.sh status

set -euo pipefail

CONFIG="/boot/firmware/config.txt"
BACKUP="/boot/firmware/config.txt.bak-p7-$(date +%Y%m%d)"

cmd="${1:-status}"

case "$cmd" in
    backup)
        cp -a "$CONFIG" "$BACKUP"
        echo "BACKUP ${BACKUP}"
        ;;
    apply-2000)
        if [ ! -f "$BACKUP" ]; then
            cp -a "$CONFIG" "$BACKUP"
            echo "BACKUP ${BACKUP} (auto)"
        fi
        if grep -q '^arm_freq=' "$CONFIG"; then
            sed -i 's/^arm_freq=.*/arm_freq=2000/' "$CONFIG"
        else
            printf '\n# P7 diagnostic — revert via pi-overclock-config.sh revert\narm_freq=2000\n' >>"$CONFIG"
        fi
        grep -E '^arm_freq=|^over_voltage=' "$CONFIG" || true
        echo "APPLY arm_freq=2000 (no over_voltage change). Reboot required."
        ;;
    revert)
        if [ ! -f "$BACKUP" ]; then
            echo "ERROR: no backup at ${BACKUP}" >&2
            exit 1
        fi
        cp -a "$BACKUP" "$CONFIG"
        echo "REVERT from ${BACKUP}"
        grep -E '^arm_freq=|^over_voltage=' "$CONFIG" 2>/dev/null || echo "(no arm_freq lines)"
        echo "Reboot required to return to stock 1800."
        ;;
    status)
        echo "config:"
        grep -E '^arm_freq=|^arm_boost=|^over_voltage=' "$CONFIG" 2>/dev/null || echo "(defaults)"
        echo "live:"
        vcgencmd measure_clock arm 2>/dev/null || true
        cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null | awk '{printf "scaling_cur_freq=%s kHz (~%d MHz)\n", $1, $1/1000}'
        vcgencmd get_throttled 2>/dev/null || true
        vcgencmd measure_temp 2>/dev/null || true
        ;;
    -h | --help)
        sed -n '2,10p' "$0"
        ;;
    *)
        echo "Unknown: $cmd" >&2
        exit 2
        ;;
esac
