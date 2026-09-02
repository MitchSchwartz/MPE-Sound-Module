#!/bin/bash
# USB Audio Class 2 gadget — Surge output to tethered host PC (Approach C).
# Playback-only @ MPE_SURGE_SAMPLE_RATE (default 48000 Hz); no ALSA loopback.
#
# Channel count is MPE_USB_STEM_CHANNELS (default 2 = stereo). Above 2 the host
# receives per-loop stems as well as the master pair — see docs/USB-MULTICHANNEL-STEMS.md.
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
# Playback channel count. 2 = stereo (the historical behaviour).
#
# The 27-channel ceiling is not arbitrary and not a bandwidth limit. p_chmask is
# the UAC2 bmChannelConfig field, where every bit is a NAMED speaker position
# (bit 0 Front Left, bit 1 Front Right, ...). UAC2 defines positions for bits
# 0-26 only, and f_uac2 rejects a mask touching any bit above that:
#
#   configfs-gadget gadget.0: Error: unsupported playback channels mask
#   probe with driver configfs-gadget failed with error -22
#
# MEASURED 2026-08-30 on pi5: 27 channels bind, 28 do not; and a mask of just
# THREE channels is rejected if one of them uses bit 27 — so it is the bit
# positions that are limited, not the count. 28 channels also failed at 44100 Hz,
# where bandwidth is not close to binding (27ch x 16-bit x 48kHz is ~2.6 MB/s
# against ~24 MB/s on the high-speed link).
MPE_USB_STEM_CHANNELS="${MPE_USB_STEM_CHANNELS:-2}"
UAC2_MAX_CHANNELS=27
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
    # usb_gadget appears only after libcomposite loads; configfs may already be
    # mounted at /sys/kernel/config on boot without that subdirectory yet.
    if [ ! -d /sys/kernel/config ]; then
        run_or_echo mount -t configfs none /sys/kernel/config
    fi
    if [ ! -d "$GADGET_ROOT" ]; then
        run_or_echo modprobe libcomposite
    fi
    if [ ! -d "$GADGET_ROOT" ]; then
        log "ERROR: configfs not mounted at $GADGET_ROOT (check dtoverlay=dwc2,dr_mode=peripheral)"
        exit 1
    fi
}

# Playback channel mask for N channels: bits 0..N-1 set.
#
# Refuses anything the driver would reject at bind time. A rejected mask does
# not fail loudly at write time — it fails later, when the UDC is written, and
# the gadget simply never appears. Better to say so here with the reason.
resolve_chmask() {
    local n="$1"
    case "$n" in
        ''|*[!0-9]*)
            log "ERROR: MPE_USB_STEM_CHANNELS must be an integer, got '$n'"
            return 1
            ;;
    esac
    if [ "$n" -lt 2 ] || [ "$n" -gt "$UAC2_MAX_CHANNELS" ]; then
        log "ERROR: MPE_USB_STEM_CHANNELS=$n out of range (2-$UAC2_MAX_CHANNELS)"
        log "       UAC2 defines channel positions for mask bits 0-26 only;"
        log "       f_uac2 rejects any mask above that (MEASURED 2026-08-30)."
        return 1
    fi
    python3 -c "print((1 << $n) - 1)"
}

create_gadget() {
    local udc
    udc="$(find_udc)" || {
        log "ERROR: no UDC in /sys/class/udc/ (OTG peripheral mode enabled?)"
        exit 1
    }

    ensure_configfs

    local sample_rate="${MPE_SURGE_SAMPLE_RATE:-48000}"
    local channels="$MPE_USB_STEM_CHANNELS"
    local chmask
    chmask="$(resolve_chmask "$channels")" || exit 1

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
    echo "UAC2 ${channels}ch playback" > "configs/$CONFIG_NAME/strings/0x409/configuration"
    echo 250 > "configs/$CONFIG_NAME/MaxPower"

    mkdir -p "functions/$FUNCTION_NAME"
    echo "$sample_rate" > "functions/$FUNCTION_NAME/p_srate"
    echo 2 > "functions/$FUNCTION_NAME/p_ssize"
    echo "$chmask" > "functions/$FUNCTION_NAME/p_chmask"
    echo 0 > "functions/$FUNCTION_NAME/c_chmask"

    ln -sf "functions/$FUNCTION_NAME" "configs/$CONFIG_NAME/"

    echo "$udc" > UDC
    log "Bound UAC2 gadget on $udc (${sample_rate} Hz, ${channels}ch playback, chmask=${chmask})"
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
