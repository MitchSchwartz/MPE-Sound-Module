#!/bin/bash
# Start Touch Patch Browser — fullscreen pygame UI for 5" DSI displays

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
# shellcheck source=lib/paths.sh
source "$SCRIPT_DIR/lib/paths.sh"
# shellcheck source=lib/detect-drm-card.sh
source "$SCRIPT_DIR/lib/detect-drm-card.sh"

# Pi console / DSI: kmsdrm + Mesa EGL (see setup-touch-pi.sh apt deps).
if [ -z "${SDL_VIDEODRIVER:-}" ] && [ -z "${DISPLAY:-}" ]; then
    export SDL_VIDEODRIVER=kmsdrm
    export SDL_KMSDRM_DEVICE="${SDL_KMSDRM_DEVICE:-$(detect_drm_card_device)}"
    export SDL_KMSDRM_REQUIRE_DRM_MASTER=1
    export SDL_VIDEO_EGL=0
fi
export SDL_MOUSE_TOUCH_EVENTS=1
export MPE_TOUCH_EVDEV="${MPE_TOUCH_EVDEV:-1}"
if [ "${MPE_TOUCH_EVDEV}" = "1" ]; then
    export SDL_TOUCH_MOUSE_EVENTS=0
fi

# Windowed mode for development on a PC or Pi with desktop:
#   MPE_TOUCH_WINDOWED=1 ./scripts/start-touch-patch-browser.sh

if systemctl is-active --quiet boot-animation.service 2>/dev/null; then
    echo "Stopping OLED boot animation..."
    sudo systemctl stop boot-animation.service
fi

if systemctl is-active --quiet touch-boot-animation.service 2>/dev/null; then
    echo "Stopping touch boot splash..."
    sudo systemctl stop touch-boot-animation.service
fi

if systemctl is-active --quiet patch-browser.service 2>/dev/null; then
    echo "Stopping OLED patch browser (touch build uses a separate service)..."
    sudo systemctl stop patch-browser.service
fi

# KMS/DRM keeps the last pygame frame on the panel until something redraws.
# Clear immediately so an old UI never flashes during the python import/scan gap.
python3 -u "$MPE_MODULE_REPO/scripts/clear-dsi-framebuffer.py" 2>/dev/null || true

echo "Starting touch patch browser..."
cd "$MPE_MODULE_REPO"
python3 -u "$MPE_MODULE_REPO/touch_patch_browser.py"
