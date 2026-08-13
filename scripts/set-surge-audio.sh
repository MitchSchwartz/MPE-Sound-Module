#!/bin/bash
# Persist Surge ALSA buffer size and/or sample rate; restart gadget + Surge as needed.
#
# Usage: sudo ./scripts/set-surge-audio.sh --buffer N [--sample-rate R]
#        sudo ./scripts/set-surge-audio.sh --sample-rate R [--buffer N]
#
# Intended for NOPASSWD in sudoers (touch UI). See docs/TOUCH_PATCH_BROWSER.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

BUFFER=""
SAMPLE_RATE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --buffer)
            BUFFER="${2:?--buffer requires a value}"
            shift 2
            ;;
        --sample-rate)
            SAMPLE_RATE="${2:?--sample-rate requires a value}"
            shift 2
            ;;
        *)
            echo "Usage: $0 --buffer N | --sample-rate R (at least one required)" >&2
            exit 1
            ;;
    esac
done

if [ -z "$BUFFER" ] && [ -z "$SAMPLE_RATE" ]; then
    echo "ERROR: specify --buffer and/or --sample-rate" >&2
    exit 1
fi

is_valid_buffer() {
    case "$1" in
        32 | 64 | 128 | 256 | 512 | 768 | 1024 | 2048) return 0 ;;
        *) return 1 ;;
    esac
}

is_valid_sample_rate() {
    case "$1" in
        44100 | 48000) return 0 ;;
        *) return 1 ;;
    esac
}

ENV_FILE="/etc/mpe/mpe.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE not found — run configure-pi-paths.sh first" >&2
    exit 1
fi

if [ -n "$BUFFER" ] && ! is_valid_buffer "$BUFFER"; then
    echo "ERROR: invalid buffer size: $BUFFER" >&2
    exit 1
fi

if [ -n "$SAMPLE_RATE" ] && ! is_valid_sample_rate "$SAMPLE_RATE"; then
    echo "ERROR: invalid sample rate: $SAMPLE_RATE" >&2
    exit 1
fi

mpe_source_appliance_env
_old_rate="${MPE_SURGE_SAMPLE_RATE:-48000}"
_old_buffer="${MPE_SURGE_BUFFER_SIZE:-1024}"

_update_env_var() {
    local key="$1"
    local value="$2"
    local tmp
    tmp="$(mktemp)"
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed "s/^${key}=.*/${key}=${value}/" "$ENV_FILE" >"$tmp"
    else
        cat "$ENV_FILE" >"$tmp"
        printf '\n%s=%s\n' "$key" "$value" >>"$tmp"
    fi
    install -m 0644 "$tmp" "$ENV_FILE"
    rm -f "$tmp"
}

if [ -n "$BUFFER" ]; then
    _update_env_var MPE_SURGE_BUFFER_SIZE "$BUFFER"
    export MPE_SURGE_BUFFER_SIZE="$BUFFER"
fi

if [ -n "$SAMPLE_RATE" ]; then
    _update_env_var MPE_SURGE_SAMPLE_RATE "$SAMPLE_RATE"
    export MPE_SURGE_SAMPLE_RATE="$SAMPLE_RATE"
fi

mpe_source_appliance_env

_rate_changed=false
if [ -n "$SAMPLE_RATE" ] && [ "$SAMPLE_RATE" != "$_old_rate" ]; then
    _rate_changed=true
fi

if [ "$_rate_changed" = true ]; then
    if systemctl is-enabled --quiet usb-audio-gadget.service 2>/dev/null; then
        systemctl restart usb-audio-gadget.service
        if [ "${MPE_AUDIO_PROFILE:-standalone}" = "usb-host" ]; then
            # shellcheck source=lib/wait-for-uac2-gadget.sh
            source "$SCRIPT_DIR/lib/wait-for-uac2-gadget.sh"
            wait_for_uac2_gadget 8 || true
        fi
    fi
fi

# shellcheck source=lib/profile-switch-flag.sh
source "$SCRIPT_DIR/lib/profile-switch-flag.sh"
# shellcheck source=lib/audio-engine.sh
source "$SCRIPT_DIR/lib/audio-engine.sh"
profile_switch_flag_mark

# JACK is the only engine (spec amended 2026-08-13) — always the graph-restart
# path below; there is no ALSA-direct branch left to fall through to.
if [ -n "$BUFFER" ]; then
    _update_env_var MPE_JACK_BUFFER "$BUFFER"
    export MPE_JACK_BUFFER="$BUFFER"
fi
if ! mpe_promote_surge_planned "settings-change"; then
    echo "ERROR: audio graph change failed — check journalctl -u mpe-jackd -u surge-xt-cli" >&2
    exit 1
fi

echo -n "Applied"
[ -n "$BUFFER" ] && echo -n " buffer=$BUFFER"
[ -n "$SAMPLE_RATE" ] && echo -n " sample_rate=$SAMPLE_RATE"
echo
