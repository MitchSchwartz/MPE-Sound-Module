#!/bin/bash
# Prepare DSI kmsdrm for touch-patch-browser (systemd ExecStartPre).
# Stops boot splash, clears stale pygame DRM holders, waits for card release.
#
# Idempotent; no-op on laptop / windowed dev (MPE_TOUCH_WINDOWED=1 or DISPLAY set).

set -euo pipefail

# Windowed/dev check before paths.sh (local config/mpe.env may be incomplete on laptops).
if [ "${MPE_TOUCH_WINDOWED:-}" = "1" ] || [ -n "${DISPLAY:-}" ]; then
    echo "prepare-dsi-display: windowed/dev mode — skipping DRM prep" >&2
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"

BOOT_UNIT="touch-boot-animation.service"
DISPLAY_REQUEST_FLAG="/run/mpe-touch-display-request"
DRM_WAIT_S="${MPE_DRM_RELEASE_WAIT_S:-1.5}"
SELF_PID=$$

_log() {
    echo "prepare-dsi-display: $*" >&2
}

_should_run() {
    if [ ! -f /proc/device-tree/model ] || ! grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
        _log "not a Raspberry Pi — skipping DRM prep"
        return 1
    fi
    return 0
}

_systemctl() {
    if [ "$(id -u)" -eq 0 ]; then
        systemctl "$@" 2>/dev/null || true
    else
        sudo systemctl "$@" 2>/dev/null || true
    fi
}

_request_display_handoff() {
    local ts
    ts="$(date +%s.%N 2>/dev/null || date +%s)"
    if [ "$(id -u)" -eq 0 ]; then
        printf '%s\n' "$ts" >"$DISPLAY_REQUEST_FLAG" 2>/dev/null || true
    else
        printf '%s\n' "$ts" | sudo tee "$DISPLAY_REQUEST_FLAG" >/dev/null 2>&1 || \
            printf '%s\n' "$ts" >"$DISPLAY_REQUEST_FLAG" 2>/dev/null || true
    fi
}

_stop_boot_splash() {
    if systemctl is-active --quiet "$BOOT_UNIT" 2>/dev/null; then
        _log "requesting cooperative handoff from $BOOT_UNIT"
        _request_display_handoff
        local deadline=$((SECONDS + 4))
        while [ "$SECONDS" -lt "$deadline" ]; do
            if ! systemctl is-active --quiet "$BOOT_UNIT" 2>/dev/null; then
                return 0
            fi
            sleep 0.1
        done
        _log "stopping $BOOT_UNIT"
        _systemctl stop "$BOOT_UNIT"
        deadline=$((SECONDS + 3))
        while [ "$SECONDS" -lt "$deadline" ]; do
            if ! systemctl is-active --quiet "$BOOT_UNIT" 2>/dev/null; then
                return 0
            fi
            sleep 0.05
        done
    fi
}

_cmdline_matches_script() {
    local pid="$1"
    local script="$2"
    local cmdline=""
    [ -d "/proc/$pid" ] || return 1
    cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
    case "$cmdline" in
        *"$script"*) return 0 ;;
        *) return 1 ;;
    esac
}

_kill_stale_script() {
    local script="$1"
    local pid signal

    for signal in TERM KILL; do
        while read -r pid; do
            [ -z "$pid" ] && continue
            [ "$pid" = "$SELF_PID" ] && continue
            _cmdline_matches_script "$pid" "$script" || continue
            _log "sending SIG$signal to stale $script pid=$pid"
            kill "-$signal" "$pid" 2>/dev/null || true
        done < <(pgrep -f "$script" 2>/dev/null || true)
        if [ "$signal" = TERM ]; then
            sleep 0.4
        fi
    done
}

_clear_stale_pygame_holders() {
    _kill_stale_script "touch_patch_browser.py"
    _kill_stale_script "touch_boot_splash.py"
}

_wait_for_drm_release() {
    _log "waiting ${DRM_WAIT_S}s for DRM release"
    sleep "$DRM_WAIT_S"
}

main() {
    if ! _should_run; then
        exit 0
    fi
    _stop_boot_splash
    _clear_stale_pygame_holders
    _wait_for_drm_release
    _log "ready"
}

main "$@"
