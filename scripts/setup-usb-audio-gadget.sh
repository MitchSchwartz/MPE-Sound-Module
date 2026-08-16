#!/bin/bash
# USB Audio Class 2 gadget — Surge output to tethered host PC (Approach C).
# Playback-only stereo @ MPE_SURGE_SAMPLE_RATE (default 48000 Hz); no ALSA loopback.
#
# Usage:
#   setup-usb-audio-gadget.sh start|stop|status|restart [--dry-run]
#
# Requires (Pi, manual one-time):
#   dtoverlay=dwc2,dr_mode=peripheral  in /boot/firmware/config.txt
#   MPE_AUDIO_PROFILE=usb-host         in /etc/mpe/mpe.env

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

GADGET_NAME="${MPE_USB_GADGET_NAME:-mpe_audio}"
GADGET_ROOT="/sys/kernel/config/usb_gadget"
GADGET_DIR="$GADGET_ROOT/$GADGET_NAME"
CONFIG_NAME="c.1"
FUNCTION_NAME="uac2.usb0"
DRY_RUN=false

log() { echo "[usb-audio-gadget] $*" >&2; }

usage() {
    echo "Usage: $0 start|stop|destroy|status|restart [--dry-run]" >&2
    exit 1
}

profile_is_usb_host() {
    case "${MPE_AUDIO_PROFILE:-standalone}" in
        usb-host | usb-host-session) return 0 ;;
        *) return 1 ;;
    esac
}

# shellcheck source=lib/gadget-persist.sh
source "$SCRIPT_DIR/lib/gadget-persist.sh"

gadget_should_bind() {
    mpe_gadget_should_bind
}

find_udc() {
    local udc
    if [ -n "${MPE_USB_GADGET_UDC:-}" ]; then
        printf '%s' "$MPE_USB_GADGET_UDC"
        return 0
    fi
    udc="$(ls /sys/class/udc/ 2>/dev/null | head -1 || true)"
    if [ -z "$udc" ]; then
        return 1
    fi
    printf '%s' "$udc"
}

gadget_bound() {
    [ -d "$GADGET_DIR" ] && [ -n "$(cat "$GADGET_DIR/UDC" 2>/dev/null || true)" ]
}

run_or_echo() {
    if [ "$DRY_RUN" = true ]; then
        log "DRY-RUN: $*"
    else
        "$@"
    fi
}

require_root() {
    if [ "$(id -u)" -ne 0 ] && [ "$DRY_RUN" = false ]; then
        log "ERROR: root required (systemd service or sudo)"
        exit 1
    fi
}

ensure_configfs() {
    if [ ! -d "$GADGET_ROOT" ]; then
        run_or_echo mount -t configfs none /sys/kernel/config
    fi
    if [ ! -d "$GADGET_ROOT" ]; then
        log "ERROR: configfs not mounted at $GADGET_ROOT (check dtoverlay=dwc2,dr_mode=peripheral)"
        exit 1
    fi
}

create_gadget() {
    local udc
    udc="$(find_udc)" || {
        log "ERROR: no UDC in /sys/class/udc/ (OTG peripheral mode enabled?)"
        exit 1
    }

    ensure_configfs
    run_or_echo modprobe libcomposite

    local sample_rate="${MPE_SURGE_SAMPLE_RATE:-48000}"

    if [ "$DRY_RUN" = true ]; then
        log "DRY-RUN: would create gadget $GADGET_NAME on UDC $udc (${sample_rate} Hz stereo UAC2)"
        return 0
    fi

    if [ -d "$GADGET_DIR" ]; then
        if gadget_bound; then
            log "Gadget already bound on $udc"
            return 0
        fi
        destroy_gadget
    fi

    mkdir -p "$GADGET_DIR"
    cd "$GADGET_DIR"

    # Linux Foundation multifunction composite (widely recognized test IDs)
    echo 0x1d6b > idVendor
    echo 0x0104 > idProduct
    echo 0x0100 > bcdDevice
    echo 0x0200 > bcdUSB

    mkdir -p strings/0x409
    echo "MPE Sound Module" > strings/0x409/manufacturer
    echo "USB Audio Passthrough" > strings/0x409/product
    echo "0001" > strings/0x409/serialnumber

    mkdir -p "configs/$CONFIG_NAME/strings/0x409"
    echo "UAC2 stereo playback" > "configs/$CONFIG_NAME/strings/0x409/configuration"
    echo 250 > "configs/$CONFIG_NAME/MaxPower"

    mkdir -p "functions/$FUNCTION_NAME"
    echo "$sample_rate" > "functions/$FUNCTION_NAME/p_srate"
    echo 2 > "functions/$FUNCTION_NAME/p_ssize"
    echo 3 > "functions/$FUNCTION_NAME/p_chmask"
    echo 0 > "functions/$FUNCTION_NAME/c_chmask"

    ln -sf "functions/$FUNCTION_NAME" "configs/$CONFIG_NAME/"

    echo "$udc" > UDC
    log "Bound UAC2 gadget on $udc (${sample_rate} Hz stereo playback)"
}

destroy_gadget() {
    if [ ! -d "$GADGET_DIR" ]; then
        return 0
    fi

    if [ "$DRY_RUN" = true ]; then
        log "DRY-RUN: would unbind and remove $GADGET_DIR"
        return 0
    fi

    if [ -f "$GADGET_DIR/UDC" ]; then
        echo "" > "$GADGET_DIR/UDC" 2>/dev/null || true
        sleep 0.3
    fi

    rm -f "$GADGET_DIR/configs/$CONFIG_NAME/$FUNCTION_NAME" 2>/dev/null || true
    rmdir "$GADGET_DIR/functions/$FUNCTION_NAME" 2>/dev/null || true
    rmdir "$GADGET_DIR/configs/$CONFIG_NAME/strings/0x409" 2>/dev/null || true
    rmdir "$GADGET_DIR/configs/$CONFIG_NAME" 2>/dev/null || true
    rmdir "$GADGET_DIR/strings/0x409" 2>/dev/null || true
    rmdir "$GADGET_DIR" 2>/dev/null || rm -rf "$GADGET_DIR"

    log "Gadget removed"
}

cmd_start() {
    if ! gadget_should_bind; then
        log "Profile ${MPE_AUDIO_PROFILE:-standalone} and MPE_USB_GADGET_PERSIST=0 — skipping gadget setup"
        exit 0
    fi
    require_root
    create_gadget
}

cmd_stop() {
    require_root
    if mpe_gadget_persist_enabled; then
        log "MPE_USB_GADGET_PERSIST=1 — leaving gadget bound (host keeps USB device)"
        return 0
    fi
    destroy_gadget
}

cmd_destroy() {
    require_root
    destroy_gadget
}

cmd_status() {
    echo "PROFILE=${MPE_AUDIO_PROFILE:-standalone}"
    echo "PERSIST=$(mpe_gadget_persist_enabled && echo 1 || echo 0)"
    if ! gadget_should_bind && ! gadget_bound; then
        echo "GADGET=skipped"
        exit 0
    fi
    if gadget_bound; then
        echo "GADGET=bound"
        echo "UDC=$(cat "$GADGET_DIR/UDC" 2>/dev/null || true)"
    elif [ -d "$GADGET_DIR" ]; then
        echo "GADGET=created-unbound"
    else
        echo "GADGET=absent"
    fi
    if [ -f /proc/asound/cards ]; then
        echo "--- /proc/asound/cards ---"
        grep -E 'UAC2|Gadget|USB Audio' /proc/asound/cards || true
    fi
}

cmd="${1:-}"
shift || true
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        *) usage ;;
    esac
done

case "$cmd" in
    start) cmd_start ;;
    stop) cmd_stop ;;
    destroy) cmd_destroy ;;
    restart) cmd_stop; cmd_start ;;
    status) cmd_status ;;
    *) usage ;;
esac
