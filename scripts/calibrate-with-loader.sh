#!/bin/bash
# Run patch normalization calibration with a fullscreen progress UI on the Pi DSI display.
#
# Stops touch-patch-browser, shows calibration_loader.py on kmsdrm, then the calibrator
# restores surge-xt-cli and touch-patch-browser when finished.
#
# Examples:
#   ./scripts/calibrate-with-loader.sh              # missing gain_db only (all scanned patches)
#   ./scripts/calibrate-with-loader.sh --force      # re-calibrate entire library
#   ./scripts/calibrate-with-loader.sh --favorites-only   # Quick Select folder only (SSH/ad-hoc)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/detect-drm-card.sh
source "$SCRIPT_DIR/lib/detect-drm-card.sh"

if [ -z "${SDL_VIDEODRIVER:-}" ] && [ -z "${DISPLAY:-}" ]; then
    export SDL_VIDEODRIVER=kmsdrm
    export SDL_KMSDRM_DEVICE="${SDL_KMSDRM_DEVICE:-$(detect_drm_card_device)}"
    export SDL_KMSDRM_REQUIRE_DRM_MASTER=1
    export SDL_VIDEO_EGL=0
fi
export SDL_MOUSE_TOUCH_EVENTS=1

if systemctl is-active --quiet touch-patch-browser.service 2>/dev/null; then
    echo "Stopping touch patch browser for calibration…"
    sudo systemctl stop touch-patch-browser.service
    sleep 0.5
fi

if systemctl is-active --quiet patch-browser.service 2>/dev/null; then
    echo "Stopping OLED patch browser…"
    sudo systemctl stop patch-browser.service
    sleep 0.5
fi

cd "$MPE_MODULE_REPO"
python3 -u "$MPE_MODULE_REPO/patch_browser/calibration_loader.py" "$@"
EXIT=$?

# Belt-and-suspenders: calibrator restores services in finally, but ensure loopback
# module is gone if calibration was interrupted or sudo unload in Python failed.
# shellcheck source=lib/unload-snd-aloop.sh
source "$SCRIPT_DIR/lib/unload-snd-aloop.sh"

exit "$EXIT"
