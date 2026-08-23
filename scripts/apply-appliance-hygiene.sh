#!/bin/bash
# Phase 0 appliance hygiene — timers, services, USB runtime PM, WiFi powersave, cmdline.
#
# Idempotent. Run on the Pi after install-units:
#   sudo ./scripts/apply-appliance-hygiene.sh
#   sudo ./scripts/apply-appliance-hygiene.sh --dry-run

set -euo pipefail

DRY=false
case "${1:-}" in
    --dry-run) DRY=true ;;
    "") ;;
    *) echo "Usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

_run() {
    if [ "$DRY" = true ]; then
        echo "would: $*"
    else
        "$@"
    fi
}

_mask_timer() {
    local unit="$1"
    if systemctl is-enabled "$unit" &>/dev/null; then
        _run systemctl mask "$unit"
        echo "masked timer: $unit"
    fi
}

_disable_unit() {
    local unit="$1"
    if systemctl is-enabled "$unit" &>/dev/null; then
        _run systemctl disable --now "$unit" 2>/dev/null || _run systemctl disable "$unit"
        echo "disabled: $unit"
    fi
}

echo "=== maintenance timers ==="
for t in apt-daily.timer apt-daily-upgrade.timer dpkg-db-backup.timer logrotate.timer \
    man-db.timer e2scrub_all.timer fstrim.timer systemd-tmpfiles-clean.timer \
    rpi-zram-writeback.timer; do
    _mask_timer "$t"
done

echo "=== prune services ==="
# avahi-daemon: keep on player boxes — mDNS (.local) SSH reachability (Pi 5 Wi‑Fi).
# Pi 4 control runs avahi enabled; do not prune here.
for u in bluetooth cron udisks2 console-setup keyboard-setup \
    cloud-init cloud-init-local cloud-config cloud-final; do
    _disable_unit "${u}.service"
done

# usb-audio-gadget: only disable when usb-host profile unused (card 5 / UAC2)
if aplay -l 2>/dev/null | grep -qi UAC2; then
    if [ "${MPE_AUDIO_PROFILE:-standalone}" != "usb-host" ]; then
        echo "usb-audio-gadget: ALSA card UAC2 present; disable only if usb-host unused"
        _disable_unit usb-audio-gadget.service
    else
        echo "usb-audio-gadget: kept (MPE_AUDIO_PROFILE=usb-host)"
    fi
else
    _disable_unit usb-audio-gadget.service
fi

echo "=== USB runtime PM -> on (audio path) ==="
for ctrl in /sys/bus/usb/devices/*/power/control; do
    [ -f "$ctrl" ] || continue
    dev="$(dirname "$ctrl")"
    base="$(basename "$dev")"
    case "$base" in
        1-1|1-1.*|usb1|usb2) ;;
        *) continue ;;
    esac
    if [ "$DRY" = true ]; then
        echo "would: echo on > $ctrl (was $(cat "$ctrl" 2>/dev/null || echo '?'))"
    else
        echo on >"$ctrl" 2>/dev/null && echo "USB PM on: $base" || true
    fi
done

echo "=== WiFi powersave off ==="
if command -v iw >/dev/null 2>&1; then
    if [ "$DRY" = true ]; then
        echo "would: iw dev wlan0 set power_save off"
    else
        iw dev wlan0 set power_save off 2>/dev/null && echo "iw power_save off" || true
    fi
fi
if command -v nmcli >/dev/null 2>&1; then
    if [ "$DRY" = true ]; then
        echo "would: nmcli radio wifi on; nmcli dev set wlan0 powersave 2"
    else
        nmcli dev set wlan0 powersave 2 2>/dev/null && echo "NetworkManager wlan0 powersave=2 (disable)" || true
    fi
fi

echo "=== cmdline HDMI disable (requires reboot) ==="
CMDLINE_FILE="/boot/firmware/cmdline.txt"
[ -f "$CMDLINE_FILE" ] || CMDLINE_FILE="/boot/cmdline.txt"
if [ -f "$CMDLINE_FILE" ]; then
    ADD=(video=HDMI-A-1:d video=HDMI-A-2:d)
    content="$(cat "$CMDLINE_FILE")"
    changed=0
    for token in "${ADD[@]}"; do
        case " $content " in
            *" $token "*) ;;
            *)
                content="$content $token"
                changed=1
                ;;
        esac
    done
    if [ "$changed" -eq 1 ]; then
        if [ "$DRY" = true ]; then
            echo "would append HDMI disable tokens to $CMDLINE_FILE"
        else
            cp -a "$CMDLINE_FILE" "${CMDLINE_FILE}.bak-hygiene-$(date +%Y%m%d-%H%M%S)"
            printf '%s\n' "$content" >"$CMDLINE_FILE"
            echo "cmdline updated: HDMI-A-1/A-2 disabled — reboot required"
        fi
    else
        echo "cmdline already has HDMI disable tokens"
    fi
fi

echo "=== kernel module blacklist (v3d) ==="
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
V3D_SRC="$REPO_ROOT/config/modprobe.d/blacklist-v3d-mpe.conf"
V3D_DST="/etc/modprobe.d/blacklist-v3d-mpe.conf"
if [ -f "$V3D_SRC" ]; then
    if [ "$DRY" = true ]; then
        echo "would: install $V3D_SRC -> $V3D_DST (reboot to unload v3d)"
    else
        _run cp "$V3D_SRC" "$V3D_DST"
        echo "installed $V3D_DST — reboot required to unload v3d"
    fi
else
    echo "warn: missing $V3D_SRC" >&2
fi

echo "=== movable IRQ affinity ==="
if [ "$DRY" = true ]; then
    echo "would: apply-movable-irq-affinity.sh"
else
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    bash "$SCRIPT_DIR/apply-movable-irq-affinity.sh"
fi

echo "=== systemd manager stop timeout (DefaultTimeoutStopSec=10s) ==="
MANAGER_SRC="$REPO_ROOT/config/systemd/mpe-appliance.conf"
MANAGER_DST="/etc/systemd/system.conf.d/mpe-appliance.conf"
if [ -f "$MANAGER_SRC" ]; then
    if [ "$DRY" = true ]; then
        echo "would: install $MANAGER_SRC -> $MANAGER_DST"
    else
        _run mkdir -p /etc/systemd/system.conf.d
        if [ ! -f "$MANAGER_DST" ] || ! cmp -s "$MANAGER_SRC" "$MANAGER_DST"; then
            _run cp "$MANAGER_SRC" "$MANAGER_DST"
            echo "installed $MANAGER_DST (DefaultTimeoutStopSec=10s)"
            _run systemctl daemon-reexec
        else
            echo "systemd manager conf already current"
        fi
    fi
else
    echo "warn: missing $MANAGER_SRC" >&2
fi

echo "apply-appliance-hygiene: done"
