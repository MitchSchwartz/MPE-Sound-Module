#!/bin/bash
# One-command Surge → Loopback routing for looper testing (standalone only).
#
#   sudo ./scripts/looper-audio-route.sh on      # Surge → snd-aloop; run mpe-looper separately
#   sudo ./scripts/looper-audio-route.sh off     # restore Sound Blaster direct path
#   ./scripts/looper-audio-route.sh status
#
# Sets MPE_LOOPER_ENABLED in /etc/mpe/mpe.env and restarts surge-xt-cli.service.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/mpe-services.sh
source "$SCRIPT_DIR/lib/mpe-services.sh"

ENV_FILE="/etc/mpe/mpe.env"

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

_require_appliance_env() {
    if [ ! -f "$ENV_FILE" ]; then
        echo "ERROR: $ENV_FILE not found — run configure-pi-paths.sh on the Pi first" >&2
        exit 1
    fi
}

_require_standalone() {
    mpe_source_appliance_env
    if [ "${MPE_AUDIO_PROFILE:-standalone}" != "standalone" ]; then
        echo "ERROR: looper routing is standalone-only (profile=${MPE_AUDIO_PROFILE})" >&2
        exit 1
    fi
}

_show_loopback_hint() {
    if [ -f "$SCRIPT_DIR/resolve-surge-loopback.py" ]; then
        python3 "$SCRIPT_DIR/resolve-surge-loopback.py" "$SURGE_CLI" 2>/dev/null \
            | sed 's/^/  Surge loopback interface: /' || true
    fi
    if [ -r /proc/asound/cards ]; then
        grep -i loopback /proc/asound/cards | sed 's/^/  /' || echo "  (no Loopback card — run: sudo modprobe snd-aloop)"
    fi
}

cmd_status() {
    mpe_source_appliance_env
    echo "MPE_LOOPER_ENABLED=${MPE_LOOPER_ENABLED:-0}"
    echo "MPE_AUDIO_PROFILE=${MPE_AUDIO_PROFILE:-standalone}"
    if systemctl is-active --quiet surge-xt-cli.service 2>/dev/null; then
        echo "surge-xt-cli.service: active"
    else
        echo "surge-xt-cli.service: inactive"
    fi
    _show_loopback_hint
    echo ""
    echo "Test: python3 $MPE_MODULE_REPO/scripts/mpe-looper.py --buffer-size \${MPE_SURGE_BUFFER_SIZE:-512}"
}

cmd_on() {
    _require_appliance_env
    _require_standalone
    mpe_source_appliance_env
    cur_buf="${MPE_SURGE_BUFFER_SIZE:-1024}"
    if [ "$cur_buf" -ne 512 ] 2>/dev/null; then
        _update_env_var MPE_SURGE_BUFFER_SIZE 512
        echo "  Looper latency budget: Surge 512 + looper 512 = 1024 samples (~21 ms @ 48 kHz)"
    fi
    _update_env_var MPE_LOOPER_ENABLED 1
    export MPE_LOOPER_ENABLED=1
    sudo modprobe snd-aloop 2>/dev/null || true
    # shellcheck source=lib/profile-switch-flag.sh
    source "$SCRIPT_DIR/lib/profile-switch-flag.sh"
    profile_switch_flag_mark
    sudo systemctl restart surge-xt-cli.service
    if [ -x "$MPE_MODULE_REPO/scripts/install-mpe-looper-service.sh" ]; then
        "$MPE_MODULE_REPO/scripts/install-mpe-looper-service.sh"
        sudo systemctl enable --now mpe-looper.service
    fi
    echo "Looper route ON — Surge → Loopback; mpe-looper.service → Sound Blaster."
    _show_loopback_hint
    echo ""
    echo "Next: looper runs as mpe-looper.service (or: python3 scripts/mpe-looper.py)"
}

cmd_off() {
    _require_appliance_env
    sudo systemctl stop mpe-looper.service 2>/dev/null || true
    sudo systemctl disable mpe-looper.service 2>/dev/null || true
    pkill -f 'scripts/mpe-looper.py' 2>/dev/null || true
    _update_env_var MPE_LOOPER_ENABLED 0
    export MPE_LOOPER_ENABLED=0
    # shellcheck source=lib/profile-switch-flag.sh
    source "$SCRIPT_DIR/lib/profile-switch-flag.sh"
    profile_switch_flag_mark
    sudo systemctl restart surge-xt-cli.service
    echo "Looper route OFF — Surge → Sound Blaster (normal standalone path)."
}

usage() {
    cat <<EOF
Usage: $0 {on|off|status}

  on      Enable MPE_LOOPER_ENABLED + snd-aloop + restart Surge on Loopback
  off     Disable looper routing + restart Surge on Sound Blaster
  status  Show flag, Surge service, loopback card

Requires standalone profile. Run mpe-looper.py in a second terminal after 'on'.
EOF
}

case "${1:-status}" in
    on | enable | 1)
        [ "$(id -u)" -eq 0 ] || { echo "ERROR: run with sudo for 'on'" >&2; exit 1; }
        cmd_on
        ;;
    off | disable | 0)
        [ "$(id -u)" -eq 0 ] || { echo "ERROR: run with sudo for 'off'" >&2; exit 1; }
        cmd_off
        ;;
    status | -h | --help | help)
        cmd_status
        ;;
    *)
        usage >&2
        exit 1
        ;;
esac
