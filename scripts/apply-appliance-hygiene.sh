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

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$REPO_ROOT/scripts"
# The appliance's canon. Overridable so a test can hand the script an env
# file of its own; nothing else should ever set it.
MPE_ENV_FILE="${MPE_ENV_FILE:-/etc/mpe/mpe.env}"

# One KEY from the appliance env file, empty when absent. `sudo` strips the
# caller's MPE_* variables, so a script that decides anything from them has to
# read the file itself.
_env_var() {
    [ -f "$MPE_ENV_FILE" ] || return 0
    grep -E "^$1=" "$MPE_ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- \
        | tr -d '"' | tr -d "'" || true
}

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
# cloud-init: Raspberry Pi OS trixie ships cloud-init 25.2 as ONE process
# (cloud-init-main.service) plus a network stage, linked from
# cloud-init.target.wants at image build. This list held only the four
# classic units until 2026-09-07, so main and network kept running every boot
# while the config and final stages never did, and `cloud-init status`
# reported "error - Failed due to systemd unit failure". MEASURED 2026-09-07
# on the SD image: no unit had failed; the error was cloud-init's own
# bookkeeping for a half-disabled layout, and cloud-init touches no
# NetworkManager profile here. The two newer units join the list, and the
# documented off switch -- the marker file the generator checks before any
# unit is considered -- makes the next boot skip cloud-init entirely and
# report "disabled" instead of "error". First-boot provisioning (user, host
# name, WiFi) is long done on any box this script runs on.
for u in bluetooth cron udisks2 console-setup keyboard-setup \
    cloud-init cloud-init-local cloud-config cloud-final \
    cloud-init-main cloud-init-network; do
    _disable_unit "${u}.service"
done
_disable_unit cloud-init-hotplugd.socket
if [ ! -e /etc/cloud/cloud-init.disabled ]; then
    _run touch /etc/cloud/cloud-init.disabled
    echo "cloud-init: off at next boot (/etc/cloud/cloud-init.disabled)"
fi

# usb-audio-gadget: the same question the unit itself asks at boot
# (setup-usb-audio-gadget.sh -> mpe_gadget_should_bind): the gadget is wanted
# when the profile routes audio to the host OR when MPE_USB_GADGET_PERSIST
# keeps the UAC2 link up for the host's DAW. Until 2026-09-07 this section
# asked a narrower question -- "is the profile usb-host?" -- of an environment
# that under `sudo` never carries MPE_AUDIO_PROFILE at all, and disabled the
# unit outright when no UAC2 card was listed. MEASURED 2026-09-07 on the SD
# image (profile standalone, persist 1, gadget enabled and active by design):
# a dry run answered "would: systemctl disable --now usb-audio-gadget.service".
# The two keys come from the appliance env file when the environment lacks
# them, the decision is the library's, and an unreadable answer keeps the
# gadget: a unit disabled by mistake is a silent host with no error anywhere.
# shellcheck source=lib/gadget-persist.sh
source "$SCRIPT_DIR/lib/gadget-persist.sh"
[ -n "${MPE_AUDIO_PROFILE+x}" ] || MPE_AUDIO_PROFILE="$(_env_var MPE_AUDIO_PROFILE)"
[ -n "${MPE_USB_GADGET_PERSIST+x}" ] || MPE_USB_GADGET_PERSIST="$(_env_var MPE_USB_GADGET_PERSIST)"
export MPE_AUDIO_PROFILE MPE_USB_GADGET_PERSIST
if mpe_gadget_should_bind; then
    echo "usb-audio-gadget: kept (profile=${MPE_AUDIO_PROFILE:-standalone} persist=${MPE_USB_GADGET_PERSIST:-1})"
else
    echo "usb-audio-gadget: not wanted (profile=${MPE_AUDIO_PROFILE:-standalone} persist=${MPE_USB_GADGET_PERSIST:-1})"
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
    # `nmcli dev set` is a runtime setting and a reboot forgets it. MEASURED
    # 2026-09-07 on the SD image, 30 s after a warm reboot with the rest of
    # this script's files already in place: `iw dev wlan0 get power_save` ->
    # "Power save: on", every WiFi profile at powersave=default. The
    # per-connection property is what survives.
    while IFS=: read -r name type; do
        [ "$type" = "802-11-wireless" ] || continue
        if [ "$DRY" = true ]; then
            echo "would: nmcli con modify \"$name\" 802-11-wireless.powersave 2"
        else
            nmcli con modify "$name" 802-11-wireless.powersave 2 2>/dev/null \
                && echo "NetworkManager \"$name\" powersave=2 (persisted)" || true
        fi
    done < <(nmcli -t -f NAME,TYPE con show 2>/dev/null || true)
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
V3D_SRC="$REPO_ROOT/config/modprobe.d/blacklist-v3d-mpe.conf"
V3D_DST="/etc/modprobe.d/blacklist-v3d-mpe.conf"
if [ -f "$V3D_SRC" ]; then
    # Say "already installed" when it is. A dry run that prints "would:
    # install" for a file already in place reads as drift that is not there
    # (2026-09-07: three of four "unapplied" items were this).
    if [ -f "$V3D_DST" ] && cmp -s "$V3D_SRC" "$V3D_DST"; then
        echo "v3d blacklist already installed ($V3D_DST)"
    elif [ "$DRY" = true ]; then
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
    bash "$SCRIPT_DIR/apply-movable-irq-affinity.sh"
fi

echo "=== systemd manager stop timeout (DefaultTimeoutStopSec=10s) ==="
MANAGER_SRC="$REPO_ROOT/config/systemd/mpe-appliance.conf"
MANAGER_DST="/etc/systemd/system.conf.d/mpe-appliance.conf"
if [ -f "$MANAGER_SRC" ]; then
    if [ -f "$MANAGER_DST" ] && cmp -s "$MANAGER_SRC" "$MANAGER_DST"; then
        echo "systemd manager conf already current ($MANAGER_DST)"
    elif [ "$DRY" = true ]; then
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
