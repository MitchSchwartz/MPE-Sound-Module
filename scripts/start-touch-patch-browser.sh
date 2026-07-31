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
    echo "Stopping OLED boot animation..." >&2
    sudo systemctl stop boot-animation.service
fi

# Keep touch-boot-animation running until the browser claims DRM (see touch_browser_app).
# Do not clear the framebuffer here — that releases kmsdrm and flashes the console.

if systemctl is-active --quiet patch-browser.service 2>/dev/null; then
    echo "Stopping OLED patch browser (touch build uses a separate service)..." >&2
    sudo systemctl stop patch-browser.service
fi

cd "$MPE_MODULE_REPO"
exec python3 -u "$MPE_MODULE_REPO/touch_patch_browser.py"
